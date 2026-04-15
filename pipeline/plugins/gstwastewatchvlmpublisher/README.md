# gstwastewatchvlmpublisher

Custom GStreamer sink element that consumes JPEG buffers and publishes a
`wastewatch.vlm.requests` Kafka message on EOS, matching the
`VLMRequest` schema in `backend/app/vlm/schemas.py`.

Used by `pipeline/app/history_publisher.py` to replay recorded historical
video clips through a decode pipeline and hand them to the VLM worker
for scene summarisation, without having to run the live DeepStream
pipeline end-to-end.

## Properties

| Property          | Type   | Default                      | Purpose                                    |
| ----------------- | ------ | ---------------------------- | ------------------------------------------ |
| `camera-id`       | string | `""`                         | Camera UUID for `VLMRequest.camera_id`     |
| `track-id`        | int    | `0`                          | Synthetic track id                         |
| `session-id`      | string | `""`                         | Track session UUID                         |
| `zone-name`       | string | `""`                         | Zone label shown in the VLM prompt         |
| `dwell-time`      | float  | `0.0`                        | Dwell time (seconds)                       |
| `class-name`      | string | `"person"`                   | Detected class name                        |
| `path-summary`    | string | `""`                         | Short textual path summary                 |
| `sample-count`    | int    | `6`                          | Frames sampled evenly across the clip      |
| `kafka-bootstrap` | string | `"kafka:9092"`               | Kafka bootstrap servers                    |
| `topic`           | string | `"wastewatch.vlm.requests"`  | Destination Kafka topic                    |

## Example pipeline

```bash
gst-launch-1.0 \
  filesrc location=/videos/VID-20260131-WA0003.mp4 \
  ! decodebin \
  ! videoconvert \
  ! videoscale \
  ! video/x-raw,width=512 \
  ! jpegenc \
  ! wastewatchvlmpublisher \
      camera-id=6c12c568-745c-4875-a1e6-7ef1f7d00fd4 \
      track-id=101 \
      session-id=5a6b6e67-b99a-4a99-a458-a4ad373d9ee8 \
      zone-name="Lake Shore Gardens - Block 1" \
      dwell-time=37 \
      path-summary="Person walks along eastern fence" \
      sample-count=6 \
      kafka-bootstrap=kafka:9092
```

## Discovery

Python-based GStreamer plugins are discovered via the `python/`
subdirectory under `GST_PLUGIN_PATH`. The harness
`pipeline/app/history_publisher.py` imports the module directly and
calls `register_element()` so the pipeline works even without a plugin
loader available.
