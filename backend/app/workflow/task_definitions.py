"""Conductor task and workflow definitions for dumping event processing."""

from __future__ import annotations

OWNER_EMAIL = "ops@civilytics.io"

TASK_DEFINITIONS: list[dict] = [
    {
        "name": "validate_dumping_event",
        "responseTimeoutSeconds": 30,
        "timeoutSeconds": 60,
        "retryCount": 2,
        "retryLogic": "FIXED",
        "retryDelaySeconds": 30,
        "inputKeys": ["event"],
        "outputKeys": ["validated_event"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "extract_evidence",
        "responseTimeoutSeconds": 90,
        "timeoutSeconds": 300,
        "retryCount": 3,
        "retryLogic": "EXPONENTIAL_BACKOFF",
        "retryDelaySeconds": 30,
        "maxRetryDelaySeconds": 600,
        "inputKeys": ["event", "validated_event"],
        "outputKeys": ["evidence_refs", "evidence_count", "integrity_state"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "run_lpr_analysis",
        "responseTimeoutSeconds": 60,
        "timeoutSeconds": 180,
        "retryCount": 2,
        "retryLogic": "EXPONENTIAL_BACKOFF",
        "retryDelaySeconds": 30,
        "maxRetryDelaySeconds": 600,
        "inputKeys": ["event", "evidence_refs"],
        "outputKeys": ["lpr_result"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "cluster_subject_sightings",
        "responseTimeoutSeconds": 60,
        "timeoutSeconds": 180,
        "retryCount": 2,
        "retryLogic": "EXPONENTIAL_BACKOFF",
        "retryDelaySeconds": 30,
        "maxRetryDelaySeconds": 600,
        "inputKeys": ["event", "evidence_refs"],
        "outputKeys": ["cluster_result"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "update_analytics",
        "responseTimeoutSeconds": 30,
        "timeoutSeconds": 60,
        "retryCount": 2,
        "retryLogic": "FIXED",
        "retryDelaySeconds": 30,
        "inputKeys": ["event", "evidence_refs", "lpr_result", "cluster_result"],
        "outputKeys": ["analytics_version", "completion_time"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "publish_event_record",
        "responseTimeoutSeconds": 30,
        "timeoutSeconds": 60,
        "retryCount": 3,
        "retryLogic": "EXPONENTIAL_BACKOFF",
        "retryDelaySeconds": 30,
        "maxRetryDelaySeconds": 600,
        "inputKeys": ["event", "evidence_refs", "lpr_result", "cluster_result", "analytics_version"],
        "outputKeys": ["event_record_id", "published_at"],
        "ownerEmail": OWNER_EMAIL,
    },
    {
        "name": "notify_workflow_failure",
        "responseTimeoutSeconds": 30,
        "timeoutSeconds": 60,
        "retryCount": 3,
        "retryLogic": "EXPONENTIAL_BACKOFF",
        "retryDelaySeconds": 30,
        "maxRetryDelaySeconds": 600,
        "inputKeys": ["workflow_id", "event_id", "failed_task_type", "severity", "reason"],
        "outputKeys": ["notification_status"],
        "ownerEmail": OWNER_EMAIL,
    },
]

# ---------------------------------------------------------------------------
# Workflow definition: dumping_event_processing v1
#
# Flow:
#   validate → extract_evidence → FORK(lpr_branch, cluster_branch) → JOIN
#   → update_analytics → publish_event_record
#
# Each fork branch uses a SWITCH to skip when not applicable.
# Failure handler: notify_workflow_failure
# ---------------------------------------------------------------------------

DUMPING_EVENT_WORKFLOW: dict = {
    "name": "dumping_event_processing",
    "version": 1,
    "ownerEmail": OWNER_EMAIL,
    "restartable": True,
    "schemaVersion": 2,
    "inputParameters": ["event"],
    "outputParameters": {
        "eventId": "${workflow.input.event.eventId}",
        "workflowId": "${workflow.workflowId}",
        "evidenceRefs": "${extract_evidence.output.evidence_refs}",
        "lprResult": "${run_lpr_analysis.output.lpr_result}",
        "clusterResult": "${cluster_subject_sightings.output.cluster_result}",
        "analyticsStatus": "${update_analytics.output.analytics_version}",
        "processingStatus": "COMPLETED",
    },
    "failureWorkflow": "notify_workflow_failure_flow",
    "tasks": [
        # 1. Validate
        {
            "name": "validate_dumping_event",
            "taskReferenceName": "validate_dumping_event",
            "type": "SIMPLE",
            "inputParameters": {
                "event": "${workflow.input.event}",
            },
        },
        # 2. Extract evidence
        {
            "name": "extract_evidence",
            "taskReferenceName": "extract_evidence",
            "type": "SIMPLE",
            "inputParameters": {
                "event": "${workflow.input.event}",
                "validated_event": "${validate_dumping_event.output.validated_event}",
            },
        },
        # 3. Fork: LPR + Clustering in parallel
        {
            "name": "parallel_analysis",
            "taskReferenceName": "parallel_analysis",
            "type": "FORK_JOIN",
            "forkTasks": [
                # Branch 1: LPR (only when vehicleVisible)
                [
                    {
                        "name": "lpr_decision",
                        "taskReferenceName": "lpr_decision",
                        "type": "SWITCH",
                        "evaluatorType": "javascript",
                        "expression": "$.vehicleVisible === true ? 'run' : 'skip'",
                        "inputParameters": {
                            "vehicleVisible": "${workflow.input.event.vehicleVisible}",
                        },
                        "decisionCases": {
                            "run": [
                                {
                                    "name": "run_lpr_analysis",
                                    "taskReferenceName": "run_lpr_analysis",
                                    "type": "SIMPLE",
                                    "inputParameters": {
                                        "event": "${workflow.input.event}",
                                        "evidence_refs": "${extract_evidence.output.evidence_refs}",
                                    },
                                },
                            ],
                        },
                        "defaultCase": [],
                    },
                ],
                # Branch 2: Clustering (when personVisible or vehicleVisible)
                [
                    {
                        "name": "cluster_decision",
                        "taskReferenceName": "cluster_decision",
                        "type": "SWITCH",
                        "evaluatorType": "javascript",
                        "expression": "($.personVisible === true || $.vehicleVisible === true) ? 'run' : 'skip'",
                        "inputParameters": {
                            "personVisible": "${workflow.input.event.personVisible}",
                            "vehicleVisible": "${workflow.input.event.vehicleVisible}",
                        },
                        "decisionCases": {
                            "run": [
                                {
                                    "name": "cluster_subject_sightings",
                                    "taskReferenceName": "cluster_subject_sightings",
                                    "type": "SIMPLE",
                                    "inputParameters": {
                                        "event": "${workflow.input.event}",
                                        "evidence_refs": "${extract_evidence.output.evidence_refs}",
                                    },
                                },
                            ],
                        },
                        "defaultCase": [],
                    },
                ],
            ],
            "joinOn": ["lpr_decision", "cluster_decision"],
        },
        # 4. Join
        {
            "name": "join_analysis",
            "taskReferenceName": "join_analysis",
            "type": "JOIN",
            "joinOn": ["lpr_decision", "cluster_decision"],
        },
        # 5. Update analytics
        {
            "name": "update_analytics",
            "taskReferenceName": "update_analytics",
            "type": "SIMPLE",
            "inputParameters": {
                "event": "${workflow.input.event}",
                "evidence_refs": "${extract_evidence.output.evidence_refs}",
                "lpr_result": "${run_lpr_analysis.output.lpr_result}",
                "cluster_result": "${cluster_subject_sightings.output.cluster_result}",
            },
        },
        # 6. Publish event record
        {
            "name": "publish_event_record",
            "taskReferenceName": "publish_event_record",
            "type": "SIMPLE",
            "inputParameters": {
                "event": "${workflow.input.event}",
                "evidence_refs": "${extract_evidence.output.evidence_refs}",
                "lpr_result": "${run_lpr_analysis.output.lpr_result}",
                "cluster_result": "${cluster_subject_sightings.output.cluster_result}",
                "analytics_version": "${update_analytics.output.analytics_version}",
            },
        },
    ],
}


async def register_all() -> None:
    """Register all task and workflow definitions with Conductor."""
    from app.workflow.conductor_client import conductor

    await conductor.register_task_definitions(TASK_DEFINITIONS)
    await conductor.register_workflow_definition(DUMPING_EVENT_WORKFLOW)
