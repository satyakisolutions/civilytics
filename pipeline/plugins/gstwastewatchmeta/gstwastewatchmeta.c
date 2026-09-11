/* Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
 * WasteWatch Custom DeepStream GStreamer Plugin
 * Proprietary and confidential.
 *
 * In-place transform plugin that enriches NvDsObjectMeta with WasteWatch-
 * specific user metadata: ROI containment, FSM state, dwell time, track info.
 * Downstream elements access this via NvDsUserMeta with type
 * NVDS_USER_META_WASTEWATCH.
 */

#ifdef HAVE_CONFIG_H
#include "config.h"
#endif

#include <string.h>
#include <stdio.h>
#include <math.h>

#include <gst/gst.h>
#include <gst/base/gstbasetransform.h>

#include "gstwastewatchmeta.h"
#include "nvdsmeta.h"
#include "nvds_analytics_meta.h"
#include "gstnvdsmeta.h"

/* --------------------------------------------------------------------------
 * Debug category
 * ----------------------------------------------------------------------- */
GST_DEBUG_CATEGORY_STATIC (gst_wastewatch_meta_debug);
#define GST_CAT_DEFAULT gst_wastewatch_meta_debug

/* --------------------------------------------------------------------------
 * Property IDs
 * ----------------------------------------------------------------------- */
enum {
    PROP_0,
    PROP_ENTER_THRESHOLD,
    PROP_OBSERVE_THRESHOLD,
    PROP_EXIT_TIMEOUT,
    PROP_LOST_TIMEOUT,
    PROP_INPUT_WIDTH,
    PROP_INPUT_HEIGHT,
    PROP_ROI_CONFIG,
};

/* Default property values */
#define DEFAULT_ENTER_THRESHOLD   5.0f
#define DEFAULT_OBSERVE_THRESHOLD 15.0f
#define DEFAULT_EXIT_TIMEOUT      300.0f
#define DEFAULT_LOST_TIMEOUT      30.0f
#define DEFAULT_INPUT_WIDTH       1920
#define DEFAULT_INPUT_HEIGHT      1080

/* --------------------------------------------------------------------------
 * Pad templates
 * ----------------------------------------------------------------------- */
static GstStaticPadTemplate sink_template = GST_STATIC_PAD_TEMPLATE (
    "sink",
    GST_PAD_SINK,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS_ANY
);

static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE (
    "src",
    GST_PAD_SRC,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS_ANY
);

/* --------------------------------------------------------------------------
 * GObject boilerplate
 * ----------------------------------------------------------------------- */
#define gst_wastewatch_meta_parent_class parent_class
G_DEFINE_TYPE (GstWasteWatchMeta, gst_wastewatch_meta, GST_TYPE_BASE_TRANSFORM);

/* Forward declarations */
static void     gst_wastewatch_meta_set_property (GObject *object, guint prop_id,
                                                   const GValue *value, GParamSpec *pspec);
static void     gst_wastewatch_meta_get_property (GObject *object, guint prop_id,
                                                   GValue *value, GParamSpec *pspec);
static void     gst_wastewatch_meta_finalize     (GObject *object);
static gboolean gst_wastewatch_meta_start        (GstBaseTransform *trans);
static gboolean gst_wastewatch_meta_stop         (GstBaseTransform *trans);
static GstFlowReturn gst_wastewatch_meta_transform_ip (GstBaseTransform *trans,
                                                        GstBuffer *buf);

/* --------------------------------------------------------------------------
 * ROI polygon structure
 * ----------------------------------------------------------------------- */
typedef struct _RoiPoint {
    gfloat x;
    gfloat y;
} RoiPoint;

typedef struct _RoiPolygon {
    gchar   name[64];
    GArray *points;   /* array of RoiPoint, normalized 0-1 */
} RoiPolygon;

/* --------------------------------------------------------------------------
 * Compound hash key: camera_id + track_id
 * ----------------------------------------------------------------------- */
typedef struct _TrackKey {
    gchar   camera_id[32];
    guint64 track_id;
} TrackKey;

static guint
track_key_hash (gconstpointer key)
{
    const TrackKey *k = (const TrackKey *) key;
    guint h = g_str_hash (k->camera_id);
    h ^= g_int64_hash (&k->track_id);
    return h;
}

static gboolean
track_key_equal (gconstpointer a, gconstpointer b)
{
    const TrackKey *ka = (const TrackKey *) a;
    const TrackKey *kb = (const TrackKey *) b;
    return (ka->track_id == kb->track_id) && (g_strcmp0 (ka->camera_id, kb->camera_id) == 0);
}

static void
track_key_free (gpointer data)
{
    g_free (data);
}

static void
track_state_free (gpointer data)
{
    g_free (data);
}

/* --------------------------------------------------------------------------
 * ROI polygon helpers
 * ----------------------------------------------------------------------- */
static void
roi_polygon_free (gpointer data)
{
    RoiPolygon *poly = (RoiPolygon *) data;
    if (poly->points)
        g_array_unref (poly->points);
    g_free (poly);
}

static void
roi_list_free (gpointer data)
{
    GList *list = (GList *) data;
    g_list_free_full (list, roi_polygon_free);
}

/**
 * Point-in-polygon test using the ray-casting algorithm.
 * Matches the Python implementation in pipeline/app/roi.py.
 */
static gboolean
point_in_polygon (gfloat px, gfloat py, GArray *points)
{
    guint n = points->len;
    if (n < 3)
        return FALSE;

    gboolean inside = FALSE;
    guint j = n - 1;

    for (guint i = 0; i < n; i++) {
        RoiPoint *pi_pt = &g_array_index (points, RoiPoint, i);
        RoiPoint *pj_pt = &g_array_index (points, RoiPoint, j);

        if (((pi_pt->y > py) != (pj_pt->y > py)) &&
            (px < (pj_pt->x - pi_pt->x) * (py - pi_pt->y) / (pj_pt->y - pi_pt->y) + pi_pt->x)) {
            inside = !inside;
        }
        j = i;
    }
    return inside;
}

/**
 * Check if a point is inside any ROI polygon for a given camera.
 * If inside, copies the ROI name into roi_name_out (max 64 chars).
 * Returns TRUE if inside any polygon.
 */
static gboolean
check_roi_containment (GstWasteWatchMeta *self, const gchar *camera_id,
                       gfloat norm_x, gfloat norm_y, gchar *roi_name_out)
{
    GList *polys = NULL;

    if (self->roi_polygons)
        polys = (GList *) g_hash_table_lookup (self->roi_polygons, camera_id);

    if (!polys) {
        roi_name_out[0] = '\0';
        return FALSE;
    }

    for (GList *l = polys; l != NULL; l = l->next) {
        RoiPolygon *poly = (RoiPolygon *) l->data;
        if (point_in_polygon (norm_x, norm_y, poly->points)) {
            g_strlcpy (roi_name_out, poly->name, 64);
            return TRUE;
        }
    }

    roi_name_out[0] = '\0';
    return FALSE;
}

/* --------------------------------------------------------------------------
 * ROI config JSON loading
 *
 * Expected format (matches pipeline/app/roi.py ROIPolygon):
 * {
 *   "camera_id_1": [
 *     {
 *       "name": "zone-a",
 *       "points": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9], [0.1, 0.9]]
 *     }
 *   ],
 *   "camera_id_2": [ ... ]
 * }
 *
 * We use a minimal JSON parser (GLib's JsonParser is in json-glib, which
 * may not be available). Instead we parse with a simple state machine for
 * this specific flat structure to avoid adding a dependency.
 * ----------------------------------------------------------------------- */

/* Simple helper: skip whitespace */
static const gchar *
skip_ws (const gchar *p)
{
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
        p++;
    return p;
}

/* Simple helper: parse a quoted string, return pointer past closing quote.
 * Copies into out (max out_len). Returns NULL on failure. */
static const gchar *
parse_string (const gchar *p, gchar *out, gsize out_len)
{
    p = skip_ws (p);
    if (*p != '"')
        return NULL;
    p++;
    gsize i = 0;
    while (*p && *p != '"') {
        if (*p == '\\') {
            p++;
            if (!*p)
                return NULL;
        }
        if (i < out_len - 1)
            out[i++] = *p;
        p++;
    }
    out[i] = '\0';
    if (*p == '"')
        p++;
    return p;
}

/* Parse a float number, return pointer past the number. */
static const gchar *
parse_float (const gchar *p, gfloat *out)
{
    p = skip_ws (p);
    gchar *end = NULL;
    *out = (gfloat) g_ascii_strtod (p, &end);
    if (end == p)
        return NULL;
    return end;
}

/**
 * Load ROI polygons from a JSON config file.
 * Returns a GHashTable: camera_id (string) -> GList of RoiPolygon*.
 */
static GHashTable *
load_roi_config (const gchar *path)
{
    gchar *contents = NULL;
    gsize length = 0;
    GError *err = NULL;

    if (!g_file_get_contents (path, &contents, &length, &err)) {
        GST_ERROR ("Failed to read ROI config '%s': %s", path, err->message);
        g_error_free (err);
        return NULL;
    }

    GHashTable *table = g_hash_table_new_full (g_str_hash, g_str_equal, g_free, roi_list_free);

    const gchar *p = contents;
    p = skip_ws (p);
    if (*p != '{')
        goto fail;
    p++;

    while (TRUE) {
        p = skip_ws (p);
        if (*p == '}')
            break;
        if (*p == ',')
            p++;
        p = skip_ws (p);

        /* Parse camera_id key */
        gchar camera_id[32];
        p = parse_string (p, camera_id, sizeof (camera_id));
        if (!p) goto fail;
        p = skip_ws (p);
        if (*p != ':') goto fail;
        p++;
        p = skip_ws (p);

        /* Expect array of polygon objects */
        if (*p != '[') goto fail;
        p++;

        GList *poly_list = NULL;

        while (TRUE) {
            p = skip_ws (p);
            if (*p == ']')
                break;
            if (*p == ',')
                p++;
            p = skip_ws (p);

            /* Parse polygon object */
            if (*p != '{') goto fail;
            p++;

            RoiPolygon *poly = g_new0 (RoiPolygon, 1);
            poly->points = g_array_new (FALSE, FALSE, sizeof (RoiPoint));
            g_strlcpy (poly->name, "unnamed", 64);

            while (TRUE) {
                p = skip_ws (p);
                if (*p == '}')
                    break;
                if (*p == ',')
                    p++;
                p = skip_ws (p);

                gchar key[64];
                p = parse_string (p, key, sizeof (key));
                if (!p) { roi_polygon_free (poly); goto fail; }
                p = skip_ws (p);
                if (*p != ':') { roi_polygon_free (poly); goto fail; }
                p++;
                p = skip_ws (p);

                if (g_strcmp0 (key, "name") == 0) {
                    p = parse_string (p, poly->name, 64);
                    if (!p) { roi_polygon_free (poly); goto fail; }
                } else if (g_strcmp0 (key, "points") == 0) {
                    /* Parse [[x,y], [x,y], ...] */
                    if (*p != '[') { roi_polygon_free (poly); goto fail; }
                    p++;
                    while (TRUE) {
                        p = skip_ws (p);
                        if (*p == ']')
                            break;
                        if (*p == ',')
                            p++;
                        p = skip_ws (p);
                        if (*p != '[') { roi_polygon_free (poly); goto fail; }
                        p++;
                        RoiPoint pt;
                        p = parse_float (p, &pt.x);
                        if (!p) { roi_polygon_free (poly); goto fail; }
                        p = skip_ws (p);
                        if (*p == ',') p++;
                        p = parse_float (p, &pt.y);
                        if (!p) { roi_polygon_free (poly); goto fail; }
                        p = skip_ws (p);
                        if (*p != ']') { roi_polygon_free (poly); goto fail; }
                        p++;
                        g_array_append_val (poly->points, pt);
                    }
                    p++; /* skip ']' */
                } else {
                    /* Skip unknown value: string, number, or nested */
                    if (*p == '"') {
                        gchar dummy[256];
                        p = parse_string (p, dummy, sizeof (dummy));
                        if (!p) { roi_polygon_free (poly); goto fail; }
                    } else {
                        /* Skip number or token */
                        while (*p && *p != ',' && *p != '}')
                            p++;
                    }
                }
            }
            p++; /* skip '}' */

            poly_list = g_list_append (poly_list, poly);
            GST_DEBUG ("Loaded ROI polygon '%s' for camera '%s' with %u points",
                       poly->name, camera_id, poly->points->len);
        }
        p++; /* skip ']' */

        g_hash_table_insert (table, g_strdup (camera_id), poly_list);
    }

    g_free (contents);
    return table;

fail:
    GST_ERROR ("Failed to parse ROI config JSON at offset %ld", (long)(p - contents));
    g_free (contents);
    g_hash_table_destroy (table);
    return NULL;
}

/* --------------------------------------------------------------------------
 * FSM state string conversion
 * ----------------------------------------------------------------------- */
static const gchar *fsm_state_names[WW_FSM_NUM_STATES] = {
    "idle",
    "enter",
    "observe",
    "violation",
    "alerting",
    "escalated",
    "exited",
};

const gchar *
ww_fsm_state_to_string (WwFsmState state)
{
    if (state >= 0 && state < WW_FSM_NUM_STATES)
        return fsm_state_names[state];
    return "unknown";
}

/* --------------------------------------------------------------------------
 * FSM transition logic
 * Mirrors pipeline/app/event_fsm.py exactly.
 * ----------------------------------------------------------------------- */
static WwFsmState
fsm_transition (WwFsmState current, gboolean in_roi, gfloat dwell_time_s,
                gfloat enter_threshold, gfloat observe_threshold,
                gfloat exit_timeout, gdouble time_in_state)
{
    switch (current) {
    case WW_FSM_IDLE:
        if (in_roi)
            return WW_FSM_ENTER;
        break;

    case WW_FSM_ENTER:
        if (!in_roi)
            return WW_FSM_EXITED;
        if (dwell_time_s >= enter_threshold)
            return WW_FSM_OBSERVE;
        break;

    case WW_FSM_OBSERVE:
        if (!in_roi)
            return WW_FSM_EXITED;
        if (dwell_time_s >= observe_threshold)
            return WW_FSM_VIOLATION;
        break;

    case WW_FSM_VIOLATION:
        if (!in_roi)
            return WW_FSM_EXITED;
        if (time_in_state > exit_timeout)
            return WW_FSM_EXITED;
        break;

    case WW_FSM_ALERTING:
    case WW_FSM_ESCALATED:
        if (!in_roi)
            return WW_FSM_EXITED;
        break;

    case WW_FSM_EXITED:
        /* EXITED is terminal; track will be pruned */
        break;

    default:
        break;
    }
    return current;
}

/* --------------------------------------------------------------------------
 * NvDsUserMeta copy / release callbacks
 * ----------------------------------------------------------------------- */
static gpointer
ww_object_meta_copy_func (gpointer data, gpointer user_data)
{
    (void) user_data;
    WasteWatchObjectMeta *src = (WasteWatchObjectMeta *) data;
    WasteWatchObjectMeta *dst = g_malloc (sizeof (WasteWatchObjectMeta));
    memcpy (dst, src, sizeof (WasteWatchObjectMeta));
    return (gpointer) dst;
}

static void
ww_object_meta_release_func (gpointer data, gpointer user_data)
{
    (void) user_data;
    g_free (data);
}

static gpointer
ww_frame_meta_copy_func (gpointer data, gpointer user_data)
{
    (void) user_data;
    WasteWatchFrameMeta *src = (WasteWatchFrameMeta *) data;
    WasteWatchFrameMeta *dst = g_malloc (sizeof (WasteWatchFrameMeta));
    memcpy (dst, src, sizeof (WasteWatchFrameMeta));
    return (gpointer) dst;
}

static void
ww_frame_meta_release_func (gpointer data, gpointer user_data)
{
    (void) user_data;
    g_free (data);
}

/* --------------------------------------------------------------------------
 * Public helpers: retrieve WasteWatch metadata from DeepStream meta
 * ----------------------------------------------------------------------- */
WasteWatchObjectMeta *
ww_get_object_meta (NvDsObjectMeta *obj_meta)
{
    if (!obj_meta)
        return NULL;

    NvDsUserMetaList *l = obj_meta->obj_user_meta_list;
    while (l) {
        NvDsUserMeta *user_meta = (NvDsUserMeta *) l->data;
        if (user_meta->base_meta.meta_type == NVDS_USER_META_WASTEWATCH) {
            return (WasteWatchObjectMeta *) user_meta->user_meta_data;
        }
        l = l->next;
    }
    return NULL;
}

WasteWatchFrameMeta *
ww_get_frame_meta (NvDsFrameMeta *frame_meta)
{
    if (!frame_meta)
        return NULL;

    NvDsUserMetaList *l = frame_meta->frame_user_meta_list;
    while (l) {
        NvDsUserMeta *user_meta = (NvDsUserMeta *) l->data;
        if (user_meta->base_meta.meta_type == NVDS_USER_META_WASTEWATCH) {
            return (WasteWatchFrameMeta *) user_meta->user_meta_data;
        }
        l = l->next;
    }
    return NULL;
}

/* --------------------------------------------------------------------------
 * Attach WasteWatchObjectMeta to an NvDsObjectMeta
 * ----------------------------------------------------------------------- */
static void
attach_object_user_meta (NvDsBatchMeta *batch_meta, NvDsObjectMeta *obj_meta,
                         WasteWatchObjectMeta *ww_meta)
{
    NvDsUserMeta *user_meta = nvds_acquire_user_meta_from_pool (batch_meta);
    if (!user_meta) {
        GST_WARNING ("Failed to acquire user meta from pool for object");
        g_free (ww_meta);
        return;
    }

    user_meta->user_meta_data = (gpointer) ww_meta;
    user_meta->base_meta.meta_type = NVDS_USER_META_WASTEWATCH;
    user_meta->base_meta.copy_func = (NvDsMetaCopyFunc) ww_object_meta_copy_func;
    user_meta->base_meta.release_func = (NvDsMetaReleaseFunc) ww_object_meta_release_func;

    nvds_add_user_meta_to_obj (obj_meta, user_meta);
}

/* --------------------------------------------------------------------------
 * Attach WasteWatchFrameMeta to an NvDsFrameMeta
 * ----------------------------------------------------------------------- */
static void
attach_frame_user_meta (NvDsBatchMeta *batch_meta, NvDsFrameMeta *frame_meta,
                        WasteWatchFrameMeta *ww_frame)
{
    NvDsUserMeta *user_meta = nvds_acquire_user_meta_from_pool (batch_meta);
    if (!user_meta) {
        GST_WARNING ("Failed to acquire user meta from pool for frame");
        g_free (ww_frame);
        return;
    }

    user_meta->user_meta_data = (gpointer) ww_frame;
    user_meta->base_meta.meta_type = NVDS_USER_META_WASTEWATCH;
    user_meta->base_meta.copy_func = (NvDsMetaCopyFunc) ww_frame_meta_copy_func;
    user_meta->base_meta.release_func = (NvDsMetaReleaseFunc) ww_frame_meta_release_func;

    nvds_add_user_meta_to_frame (frame_meta, user_meta);
}

/* --------------------------------------------------------------------------
 * Per-camera track table helpers
 *
 * camera_states: camera_id (str) → GHashTable(track_id_key → WwTrackState*)
 * Each camera is fully isolated — bulk cleanup when camera is removed.
 * ----------------------------------------------------------------------- */

/* Get or create the per-camera track table */
static GHashTable *
get_or_create_camera_table (GstWasteWatchMeta *self, const gchar *camera_id)
{
    GHashTable *tracks = (GHashTable *) g_hash_table_lookup (self->camera_states, camera_id);
    if (tracks)
        return tracks;

    /* Create new per-camera track table: track_id(guint64) → WwTrackState* */
    tracks = g_hash_table_new_full (g_int64_hash, g_int64_equal, g_free, g_free);
    g_hash_table_insert (self->camera_states, g_strdup (camera_id), tracks);
    GST_DEBUG ("Created track table for camera %s", camera_id);
    return tracks;
}

/* Get or create track state within a camera */
static WwTrackState *
get_or_create_track (GstWasteWatchMeta *self, const gchar *camera_id,
                     guint64 track_id, const gchar *class_name, gdouble now)
{
    GHashTable *tracks = get_or_create_camera_table (self, camera_id);

    guint64 *lookup_key = g_new (guint64, 1);
    *lookup_key = track_id;

    WwTrackState *state = (WwTrackState *) g_hash_table_lookup (tracks, lookup_key);
    if (state) {
        g_free (lookup_key);
        state->last_seen = now;
        return state;
    }

    /* Create new track state */
    state = g_new0 (WwTrackState, 1);
    state->track_id = track_id;
    state->fsm_state = WW_FSM_IDLE;
    state->first_seen = now;
    state->last_seen = now;
    state->roi_entered_at = 0.0;
    state->in_roi = FALSE;
    g_strlcpy (state->class_name, class_name, sizeof (state->class_name));

    g_hash_table_insert (tracks, lookup_key, state);
    GST_LOG ("New track: camera=%s track_id=%lu class=%s", camera_id, track_id, class_name);

    return state;
}

/* --------------------------------------------------------------------------
 * Prune stale tracks — iterates per-camera, then per-track within each
 * ----------------------------------------------------------------------- */
static void
prune_stale_tracks (GstWasteWatchMeta *self, gdouble now)
{
    GHashTableIter cam_iter;
    gpointer cam_key, cam_value;
    GList *empty_cameras = NULL;

    g_hash_table_iter_init (&cam_iter, self->camera_states);
    while (g_hash_table_iter_next (&cam_iter, &cam_key, &cam_value)) {
        GHashTable *tracks = (GHashTable *) cam_value;
        const gchar *camera_id = (const gchar *) cam_key;
        GHashTableIter trk_iter;
        gpointer trk_key, trk_value;
        GList *stale_tracks = NULL;

        g_hash_table_iter_init (&trk_iter, tracks);
        while (g_hash_table_iter_next (&trk_iter, &trk_key, &trk_value)) {
            WwTrackState *ts = (WwTrackState *) trk_value;
            gdouble elapsed = now - ts->last_seen;
            if (elapsed > (gdouble) self->lost_timeout_s) {
                GST_DEBUG ("Pruning stale track %s/%lu (%.1fs since last seen)",
                           camera_id, ts->track_id, elapsed);
                stale_tracks = g_list_prepend (stale_tracks, trk_key);
            }
        }

        for (GList *l = stale_tracks; l != NULL; l = l->next) {
            g_hash_table_remove (tracks, l->data);
        }
        g_list_free (stale_tracks);

        /* Mark empty camera tables for cleanup */
        if (g_hash_table_size (tracks) == 0) {
            empty_cameras = g_list_prepend (empty_cameras, cam_key);
        }
    }

    /* Remove empty camera entries */
    for (GList *l = empty_cameras; l != NULL; l = l->next) {
        GST_DEBUG ("Removing empty camera table: %s", (const gchar *) l->data);
        g_hash_table_remove (self->camera_states, l->data);
    }
    g_list_free (empty_cameras);
}

/* --------------------------------------------------------------------------
 * Get camera_id string from source_id (pad_index)
 * ----------------------------------------------------------------------- */
static void
camera_id_from_source (guint source_id, gchar *buf, gsize buf_len)
{
    g_snprintf (buf, buf_len, "cam_%u", source_id);
}

/* --------------------------------------------------------------------------
 * Get current monotonic time in seconds
 * ----------------------------------------------------------------------- */
static gdouble
monotonic_time_s (void)
{
    return (gdouble) g_get_monotonic_time () / 1000000.0;
}

/* --------------------------------------------------------------------------
 * transform_ip: the core processing callback
 * ----------------------------------------------------------------------- */
static GstFlowReturn
gst_wastewatch_meta_transform_ip (GstBaseTransform *trans, GstBuffer *buf)
{
    GstWasteWatchMeta *self = GST_WASTEWATCH_META (trans);

    NvDsBatchMeta *batch_meta = gst_buffer_get_nvds_batch_meta (buf);
    if (!batch_meta) {
        GST_LOG_OBJECT (self, "No NvDsBatchMeta on buffer, passing through");
        return GST_FLOW_OK;
    }

    gdouble now = monotonic_time_s ();

    g_mutex_lock (&self->state_lock);

    /* Iterate over all frames in the batch */
    NvDsFrameMetaList *frame_l = batch_meta->frame_meta_list;
    while (frame_l) {
        NvDsFrameMeta *frame_meta = (NvDsFrameMeta *) frame_l->data;
        if (!frame_meta) {
            frame_l = frame_l->next;
            continue;
        }

        gchar camera_id[32];
        camera_id_from_source (frame_meta->source_id, camera_id, sizeof (camera_id));

        /* Frame-level counters */
        guint total_detections = 0;
        guint objects_in_roi = 0;
        guint active_violations = 0;
        guint active_tracks = 0;

        /* Iterate over all objects in this frame */
        NvDsObjectMetaList *obj_l = frame_meta->obj_meta_list;
        while (obj_l) {
            NvDsObjectMeta *obj_meta = (NvDsObjectMeta *) obj_l->data;
            if (!obj_meta) {
                obj_l = obj_l->next;
                continue;
            }

            total_detections++;

            /* Compute normalized bbox center */
            gfloat cx = 0.0f, cy = 0.0f;
            if (self->input_width > 0 && self->input_height > 0) {
                cx = (obj_meta->rect_params.left + obj_meta->rect_params.width / 2.0f)
                     / (gfloat) self->input_width;
                cy = (obj_meta->rect_params.top + obj_meta->rect_params.height / 2.0f)
                     / (gfloat) self->input_height;
            }

            /* ROI containment check */
            gchar roi_name[64];
            gboolean in_roi = check_roi_containment (self, camera_id, cx, cy, roi_name);

            /* Get or create track state */
            guint64 track_id = obj_meta->object_id;
            const gchar *class_name = obj_meta->obj_label;
            WwTrackState *ts = get_or_create_track (self, camera_id, track_id,
                                                     class_name, now);

            /* Track ROI entry/exit timestamps */
            if (in_roi && !ts->in_roi) {
                ts->roi_entered_at = now;
            }
            ts->in_roi = in_roi;

            /* Compute dwell time (time spent in ROI) */
            gfloat dwell_time = 0.0f;
            if (in_roi && ts->roi_entered_at > 0.0) {
                dwell_time = (gfloat) (now - ts->roi_entered_at);
            }

            /* Track age */
            gfloat track_age = (gfloat) (now - ts->first_seen);

            /* FSM transition */
            WwFsmState prev_state = ts->fsm_state;
            gdouble time_in_state = now - ts->last_seen; /* approximate */
            WwFsmState new_state = fsm_transition (
                ts->fsm_state, in_roi, dwell_time,
                self->enter_threshold_s, self->observe_threshold_s,
                self->exit_timeout_s, (gdouble) track_age);
            ts->fsm_state = new_state;

            gboolean state_changed = (new_state != prev_state);
            gboolean is_violation = (new_state == WW_FSM_VIOLATION ||
                                     new_state == WW_FSM_ALERTING ||
                                     new_state == WW_FSM_ESCALATED);

            /* Determine trigger flags */
            gboolean needs_vlm = (state_changed && new_state == WW_FSM_VIOLATION);
            gboolean needs_lpr = FALSE;

            /* Trigger LPR for vehicles entering violation state */
            if (needs_vlm) {
                if (g_strcmp0 (class_name, "car") == 0 ||
                    g_strcmp0 (class_name, "truck") == 0 ||
                    g_strcmp0 (class_name, "bus") == 0 ||
                    g_strcmp0 (class_name, "vehicle") == 0 ||
                    g_strcmp0 (class_name, "auto") == 0) {
                    needs_lpr = TRUE;
                }
            }

            if (state_changed) {
                GST_INFO_OBJECT (self,
                    "FSM %s track=%lu: %s -> %s (dwell=%.1fs)",
                    camera_id, track_id,
                    ww_fsm_state_to_string (prev_state),
                    ww_fsm_state_to_string (new_state),
                    dwell_time);
            }

            /* Update frame counters */
            if (in_roi)
                objects_in_roi++;
            if (is_violation)
                active_violations++;
            active_tracks++;

            /* Allocate and fill WasteWatchObjectMeta */
            WasteWatchObjectMeta *ww_obj = g_new0 (WasteWatchObjectMeta, 1);
            ww_obj->in_roi = in_roi;
            g_strlcpy (ww_obj->roi_name, roi_name, sizeof (ww_obj->roi_name));
            ww_obj->fsm_state = new_state;
            ww_obj->fsm_prev_state = prev_state;
            ww_obj->fsm_state_changed = state_changed;
            ww_obj->track_id = track_id;
            g_strlcpy (ww_obj->class_name, class_name, sizeof (ww_obj->class_name));
            ww_obj->dwell_time_s = dwell_time;
            ww_obj->track_age_s = track_age;
            ww_obj->bbox_center_x = cx;
            ww_obj->bbox_center_y = cy;
            ww_obj->is_violation = is_violation;
            ww_obj->needs_vlm = needs_vlm;
            ww_obj->needs_lpr = needs_lpr;
            ww_obj->first_seen_ts = ts->first_seen;
            ww_obj->roi_entered_ts = ts->roi_entered_at;
            ww_obj->frame_ts = now;

            /* Attach to NvDsObjectMeta */
            attach_object_user_meta (batch_meta, obj_meta, ww_obj);

            obj_l = obj_l->next;
        } /* end object loop */

        /* Create and attach frame-level summary */
        WasteWatchFrameMeta *ww_frame = g_new0 (WasteWatchFrameMeta, 1);
        g_strlcpy (ww_frame->camera_id, camera_id, sizeof (ww_frame->camera_id));
        ww_frame->total_detections = total_detections;
        ww_frame->objects_in_roi = objects_in_roi;
        ww_frame->active_violations = active_violations;
        ww_frame->active_tracks = active_tracks;
        ww_frame->frame_ts = now;

        attach_frame_user_meta (batch_meta, frame_meta, ww_frame);

        GST_LOG_OBJECT (self,
            "Frame %s: detections=%u in_roi=%u violations=%u tracks=%u",
            camera_id, total_detections, objects_in_roi,
            active_violations, active_tracks);

        frame_l = frame_l->next;
    } /* end frame loop */

    /* Periodically prune stale tracks */
    prune_stale_tracks (self, now);

    g_mutex_unlock (&self->state_lock);

    return GST_FLOW_OK;
}

/* --------------------------------------------------------------------------
 * GObject property set
 * ----------------------------------------------------------------------- */
static void
gst_wastewatch_meta_set_property (GObject *object, guint prop_id,
                                   const GValue *value, GParamSpec *pspec)
{
    GstWasteWatchMeta *self = GST_WASTEWATCH_META (object);

    switch (prop_id) {
    case PROP_ENTER_THRESHOLD:
        self->enter_threshold_s = g_value_get_float (value);
        break;
    case PROP_OBSERVE_THRESHOLD:
        self->observe_threshold_s = g_value_get_float (value);
        break;
    case PROP_EXIT_TIMEOUT:
        self->exit_timeout_s = g_value_get_float (value);
        break;
    case PROP_LOST_TIMEOUT:
        self->lost_timeout_s = g_value_get_float (value);
        break;
    case PROP_INPUT_WIDTH:
        self->input_width = g_value_get_uint (value);
        break;
    case PROP_INPUT_HEIGHT:
        self->input_height = g_value_get_uint (value);
        break;
    case PROP_ROI_CONFIG:
        g_free (self->roi_config_path);
        self->roi_config_path = g_value_dup_string (value);
        /* Reload ROI config if path changed */
        if (self->roi_polygons) {
            g_hash_table_destroy (self->roi_polygons);
            self->roi_polygons = NULL;
        }
        if (self->roi_config_path && self->roi_config_path[0] != '\0') {
            self->roi_polygons = load_roi_config (self->roi_config_path);
            if (self->roi_polygons) {
                GST_INFO_OBJECT (self, "Loaded ROI config from '%s' (%u cameras)",
                                 self->roi_config_path,
                                 g_hash_table_size (self->roi_polygons));
            }
        }
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
        break;
    }
}

/* --------------------------------------------------------------------------
 * GObject property get
 * ----------------------------------------------------------------------- */
static void
gst_wastewatch_meta_get_property (GObject *object, guint prop_id,
                                   GValue *value, GParamSpec *pspec)
{
    GstWasteWatchMeta *self = GST_WASTEWATCH_META (object);

    switch (prop_id) {
    case PROP_ENTER_THRESHOLD:
        g_value_set_float (value, self->enter_threshold_s);
        break;
    case PROP_OBSERVE_THRESHOLD:
        g_value_set_float (value, self->observe_threshold_s);
        break;
    case PROP_EXIT_TIMEOUT:
        g_value_set_float (value, self->exit_timeout_s);
        break;
    case PROP_LOST_TIMEOUT:
        g_value_set_float (value, self->lost_timeout_s);
        break;
    case PROP_INPUT_WIDTH:
        g_value_set_uint (value, self->input_width);
        break;
    case PROP_INPUT_HEIGHT:
        g_value_set_uint (value, self->input_height);
        break;
    case PROP_ROI_CONFIG:
        g_value_set_string (value, self->roi_config_path);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
        break;
    }
}

/* --------------------------------------------------------------------------
 * start / stop virtual methods
 * ----------------------------------------------------------------------- */
static gboolean
gst_wastewatch_meta_start (GstBaseTransform *trans)
{
    GstWasteWatchMeta *self = GST_WASTEWATCH_META (trans);

    GST_INFO_OBJECT (self, "Starting WasteWatch meta enrichment plugin");
    GST_INFO_OBJECT (self, "  enter_threshold=%.1fs observe_threshold=%.1fs",
                     self->enter_threshold_s, self->observe_threshold_s);
    GST_INFO_OBJECT (self, "  exit_timeout=%.1fs lost_timeout=%.1fs",
                     self->exit_timeout_s, self->lost_timeout_s);
    GST_INFO_OBJECT (self, "  input=%ux%u roi_config=%s",
                     self->input_width, self->input_height,
                     self->roi_config_path ? self->roi_config_path : "(none)");

    /* Load ROI config if specified and not already loaded */
    if (self->roi_config_path && !self->roi_polygons) {
        self->roi_polygons = load_roi_config (self->roi_config_path);
        if (self->roi_polygons) {
            GST_INFO_OBJECT (self, "Loaded ROI config with %u camera entries",
                             g_hash_table_size (self->roi_polygons));
        } else {
            GST_WARNING_OBJECT (self, "No ROI polygons loaded; all objects will be outside ROI");
        }
    }

    return TRUE;
}

static gboolean
gst_wastewatch_meta_stop (GstBaseTransform *trans)
{
    GstWasteWatchMeta *self = GST_WASTEWATCH_META (trans);

    g_mutex_lock (&self->state_lock);
    guint n_cameras = g_hash_table_size (self->camera_states);
    g_hash_table_remove_all (self->camera_states);
    g_mutex_unlock (&self->state_lock);
    GST_INFO_OBJECT (self, "Cleared track state for %u cameras", n_cameras);

    GST_INFO_OBJECT (self, "Stopped WasteWatch meta plugin (cleared %u tracks)", n_tracks);
    return TRUE;
}

/* --------------------------------------------------------------------------
 * finalize
 * ----------------------------------------------------------------------- */
static void
gst_wastewatch_meta_finalize (GObject *object)
{
    GstWasteWatchMeta *self = GST_WASTEWATCH_META (object);

    if (self->camera_states) {
        g_hash_table_destroy (self->camera_states);
        self->camera_states = NULL;
    }

    if (self->roi_polygons) {
        g_hash_table_destroy (self->roi_polygons);
        self->roi_polygons = NULL;
    }

    g_free (self->roi_config_path);
    self->roi_config_path = NULL;

    g_mutex_clear (&self->state_lock);

    G_OBJECT_CLASS (parent_class)->finalize (object);
}

/* --------------------------------------------------------------------------
 * Class init: install properties, set virtual methods
 * ----------------------------------------------------------------------- */
static void
gst_wastewatch_meta_class_init (GstWasteWatchMetaClass *klass)
{
    GObjectClass *gobject_class = G_OBJECT_CLASS (klass);
    GstElementClass *element_class = GST_ELEMENT_CLASS (klass);
    GstBaseTransformClass *bt_class = GST_BASE_TRANSFORM_CLASS (klass);

    gobject_class->set_property = gst_wastewatch_meta_set_property;
    gobject_class->get_property = gst_wastewatch_meta_get_property;
    gobject_class->finalize = gst_wastewatch_meta_finalize;

    bt_class->start = GST_DEBUG_FUNCPTR (gst_wastewatch_meta_start);
    bt_class->stop = GST_DEBUG_FUNCPTR (gst_wastewatch_meta_stop);
    bt_class->transform_ip = GST_DEBUG_FUNCPTR (gst_wastewatch_meta_transform_ip);

    /* This element does not change the data, only attaches metadata */
    bt_class->passthrough_on_same_caps = TRUE;

    /* Install properties */
    g_object_class_install_property (gobject_class, PROP_ENTER_THRESHOLD,
        g_param_spec_float ("enter-threshold", "Enter Threshold",
            "Dwell time (seconds) in ROI before transitioning from ENTER to OBSERVE",
            0.0f, 3600.0f, DEFAULT_ENTER_THRESHOLD,
            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property (gobject_class, PROP_OBSERVE_THRESHOLD,
        g_param_spec_float ("observe-threshold", "Observe Threshold",
            "Dwell time (seconds) in ROI before transitioning from OBSERVE to VIOLATION",
            0.0f, 3600.0f, DEFAULT_OBSERVE_THRESHOLD,
            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property (gobject_class, PROP_EXIT_TIMEOUT,
        g_param_spec_float ("exit-timeout", "Exit Timeout",
            "Maximum time (seconds) a VIOLATION state can persist before auto-exit",
            0.0f, 86400.0f, DEFAULT_EXIT_TIMEOUT,
            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property (gobject_class, PROP_LOST_TIMEOUT,
        g_param_spec_float ("lost-timeout", "Lost Timeout",
            "Seconds without seeing a track before pruning it from state",
            0.0f, 3600.0f, DEFAULT_LOST_TIMEOUT,
            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property (gobject_class, PROP_INPUT_WIDTH,
        g_param_spec_uint ("input-width", "Input Width",
            "Width of the input frame in pixels (for bbox normalization)",
            1, 7680, DEFAULT_INPUT_WIDTH,
            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property (gobject_class, PROP_INPUT_HEIGHT,
        g_param_spec_uint ("input-height", "Input Height",
            "Height of the input frame in pixels (for bbox normalization)",
            1, 4320, DEFAULT_INPUT_HEIGHT,
            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property (gobject_class, PROP_ROI_CONFIG,
        g_param_spec_string ("roi-config", "ROI Config Path",
            "Path to ROI polygon JSON configuration file",
            NULL,
            G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    /* Pad templates */
    gst_element_class_add_static_pad_template (element_class, &sink_template);
    gst_element_class_add_static_pad_template (element_class, &src_template);

    gst_element_class_set_static_metadata (element_class,
        "WasteWatch Meta Enrichment",
        "Filter/Metadata",
        "Enriches NvDsObjectMeta with WasteWatch ROI, FSM, dwell, and track metadata",
        "Satyaki Solutions Pvt Ltd <engineering@satyaki.io>");
}

/* --------------------------------------------------------------------------
 * Instance init
 * ----------------------------------------------------------------------- */
static void
gst_wastewatch_meta_init (GstWasteWatchMeta *self)
{
    self->enter_threshold_s = DEFAULT_ENTER_THRESHOLD;
    self->observe_threshold_s = DEFAULT_OBSERVE_THRESHOLD;
    self->exit_timeout_s = DEFAULT_EXIT_TIMEOUT;
    self->lost_timeout_s = DEFAULT_LOST_TIMEOUT;
    self->input_width = DEFAULT_INPUT_WIDTH;
    self->input_height = DEFAULT_INPUT_HEIGHT;
    self->roi_config_path = NULL;
    self->roi_polygons = NULL;

    /* Outer: camera_id (str) → GHashTable(track_id → WwTrackState)
     * Inner tables are destroyed via (GDestroyNotify) g_hash_table_destroy */
    self->camera_states = g_hash_table_new_full (
        g_str_hash, g_str_equal,
        g_free, (GDestroyNotify) g_hash_table_destroy);

    g_mutex_init (&self->state_lock);

    /* In-place transform: we only attach metadata, do not modify buffer data */
    gst_base_transform_set_in_place (GST_BASE_TRANSFORM (self), TRUE);
    gst_base_transform_set_passthrough (GST_BASE_TRANSFORM (self), TRUE);
}

/* --------------------------------------------------------------------------
 * Plugin registration
 * ----------------------------------------------------------------------- */
static gboolean
plugin_init (GstPlugin *plugin)
{
    GST_DEBUG_CATEGORY_INIT (gst_wastewatch_meta_debug, "wastewatchmeta",
                             0, "WasteWatch metadata enrichment");

    return gst_element_register (plugin, "wastewatchmeta",
                                 GST_RANK_NONE, GST_TYPE_WASTEWATCH_META);
}

GST_PLUGIN_DEFINE (
    GST_VERSION_MAJOR,
    GST_VERSION_MINOR,
    wastewatchmeta,
    "WasteWatch DeepStream metadata enrichment plugin",
    plugin_init,
    "1.0.0",
    "Proprietary",
    "WasteWatch",
    "https://satyaki.io"
)
