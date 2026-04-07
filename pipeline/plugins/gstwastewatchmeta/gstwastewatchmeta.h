/* Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
 * Custom DeepStream GStreamer plugin for WasteWatch metadata enrichment.
 *
 * This plugin processes NvDsBatchMeta in-place, enriching each NvDsObjectMeta
 * with WasteWatch-specific user metadata (ROI status, FSM state, dwell time,
 * track info). Downstream elements access this via NvDsUserMeta queries.
 */

#ifndef __GST_WASTEWATCH_META_H__
#define __GST_WASTEWATCH_META_H__

#include <gst/gst.h>
#include <gst/base/gstbasetransform.h>

/* DeepStream includes */
#include "nvdsmeta.h"
#include "nvds_analytics_meta.h"

G_BEGIN_DECLS

/* --- WasteWatch Custom Metadata --- */

/* Unique user meta type for WasteWatch object-level metadata */
#define NVDS_USER_META_WASTEWATCH  (nvds_get_user_meta_type("NVIDIA.NVINFER.USER_META_WASTEWATCH"))

/* FSM states matching pipeline/app/event_fsm.py */
typedef enum {
    WW_FSM_IDLE = 0,
    WW_FSM_ENTER,
    WW_FSM_OBSERVE,
    WW_FSM_VIOLATION,
    WW_FSM_ALERTING,
    WW_FSM_ESCALATED,
    WW_FSM_EXITED,
    WW_FSM_NUM_STATES,
} WwFsmState;

/* Per-object enriched metadata attached as NvDsUserMeta */
typedef struct _WasteWatchObjectMeta {
    /* ROI */
    gboolean    in_roi;
    gchar       roi_name[64];

    /* FSM */
    WwFsmState  fsm_state;
    WwFsmState  fsm_prev_state;
    gboolean    fsm_state_changed;   /* TRUE on the frame where transition occurred */

    /* Tracking / Dwell */
    guint64     track_id;
    gchar       class_name[64];
    gfloat      dwell_time_s;        /* seconds in ROI */
    gfloat      track_age_s;         /* seconds since first seen */
    gfloat      bbox_center_x;       /* normalized 0-1 */
    gfloat      bbox_center_y;       /* normalized 0-1 */

    /* Violation flags */
    gboolean    is_violation;
    gboolean    needs_vlm;           /* TRUE when VLM analysis should be triggered */
    gboolean    needs_lpr;           /* TRUE when LPR should be triggered (vehicles in violation) */

    /* Timestamps */
    gdouble     first_seen_ts;       /* monotonic timestamp */
    gdouble     roi_entered_ts;      /* monotonic timestamp of ROI entry */
    gdouble     frame_ts;            /* current frame timestamp */
} WasteWatchObjectMeta;

/* Per-frame summary metadata attached to NvDsFrameMeta */
typedef struct _WasteWatchFrameMeta {
    gchar       camera_id[32];
    guint       total_detections;
    guint       objects_in_roi;
    guint       active_violations;
    guint       active_tracks;
    gdouble     frame_ts;
} WasteWatchFrameMeta;


/* --- GStreamer Element --- */

#define GST_TYPE_WASTEWATCH_META             (gst_wastewatch_meta_get_type())
#define GST_WASTEWATCH_META(obj)             (G_TYPE_CHECK_INSTANCE_CAST((obj), GST_TYPE_WASTEWATCH_META, GstWasteWatchMeta))
#define GST_WASTEWATCH_META_CLASS(klass)     (G_TYPE_CHECK_CLASS_CAST((klass), GST_TYPE_WASTEWATCH_META, GstWasteWatchMetaClass))
#define GST_IS_WASTEWATCH_META(obj)          (G_TYPE_CHECK_INSTANCE_TYPE((obj), GST_TYPE_WASTEWATCH_META))
#define GST_IS_WASTEWATCH_META_CLASS(klass)  (G_TYPE_CHECK_CLASS_TYPE((klass), GST_TYPE_WASTEWATCH_META))

typedef struct _GstWasteWatchMeta      GstWasteWatchMeta;
typedef struct _GstWasteWatchMetaClass GstWasteWatchMetaClass;

/* Internal track state (maintained across frames) */
typedef struct _WwTrackState {
    guint64     track_id;
    WwFsmState  fsm_state;
    gdouble     first_seen;
    gdouble     last_seen;
    gdouble     roi_entered_at;
    gboolean    in_roi;
    gchar       class_name[64];
} WwTrackState;

struct _GstWasteWatchMeta {
    GstBaseTransform parent;

    /* Properties */
    gfloat      enter_threshold_s;   /* default 5.0 */
    gfloat      observe_threshold_s; /* default 15.0 */
    gfloat      exit_timeout_s;      /* default 300.0 */
    gfloat      lost_timeout_s;      /* default 30.0 */
    guint       input_width;         /* default 1920 */
    guint       input_height;        /* default 1080 */
    gchar      *roi_config_path;     /* path to ROI polygon JSON */

    /* Internal state */
    GHashTable *track_states;        /* (camera_id, track_id) -> WwTrackState* */
    GHashTable *roi_polygons;        /* camera_id -> GArray of ROI polygon points */
    GMutex      state_lock;
};

struct _GstWasteWatchMetaClass {
    GstBaseTransformClass parent_class;
};

GType gst_wastewatch_meta_get_type(void);

/* Helper: get WasteWatchObjectMeta from an NvDsObjectMeta */
WasteWatchObjectMeta *ww_get_object_meta(NvDsObjectMeta *obj_meta);

/* Helper: get WasteWatchFrameMeta from an NvDsFrameMeta */
WasteWatchFrameMeta *ww_get_frame_meta(NvDsFrameMeta *frame_meta);

/* Helper: FSM state to string */
const gchar *ww_fsm_state_to_string(WwFsmState state);

G_END_DECLS

#endif /* __GST_WASTEWATCH_META_H__ */
