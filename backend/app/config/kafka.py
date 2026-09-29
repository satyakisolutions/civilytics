# ---------------------------------------------------------------------------
# Kafka topic names
# ---------------------------------------------------------------------------
TOPIC_METADATA = "wastewatch.metadata"
TOPIC_EVENTS = "wastewatch.events"
TOPIC_VLM_REQUESTS = "wastewatch.vlm.requests"
TOPIC_VLM_RESULTS = "wastewatch.vlm.results"
TOPIC_ALERTS = "wastewatch.alerts"
TOPIC_LPR = "wastewatch.lpr"
TOPIC_TTS = "wastewatch.tts"
TOPIC_EVIDENCE = "wastewatch.evidence"
TOPIC_LOGS = "wastewatch.logs"
TOPIC_CONFIG_UPDATES = "wastewatch.config.updates"
TOPIC_SIP_CALLS = "wastewatch.sip"
TOPIC_CAMERA_STATUS = "wastewatch.camera.status"
TOPIC_HEALTH = "wastewatch.health"
TOPIC_HEALING = "wastewatch.healing"
TOPIC_MISSES = "wastewatch.misses"
TOPIC_FACES_REQUESTS = "wastewatch.faces.requests"

# ---------------------------------------------------------------------------
# Kafka consumer group IDs
# ---------------------------------------------------------------------------
GROUP_API_METADATA = "wastewatch.group.api-metadata"
GROUP_EVENT_FSM = "wastewatch.group.event-fsm"
GROUP_VLM_WORKER = "wastewatch.group.vlm-worker"
GROUP_ALERT_DISPATCHER = "wastewatch.group.alert-dispatcher"
GROUP_LOG_INGESTION = "wastewatch.group.log-ingestion"
GROUP_ESCALATION = "wastewatch.group.escalation"
GROUP_HEALTH_MONITOR = "wastewatch.group.health-monitor"
GROUP_HEALER = "wastewatch.group.healer"
GROUP_SUBJECT_WORKER = "wastewatch.group.subject-worker"
