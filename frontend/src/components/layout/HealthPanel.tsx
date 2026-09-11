// Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
import { useState, useEffect, useCallback } from "react";
import { API_BASE } from "@/lib/api-base";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ServiceStatus = "healthy" | "down" | "degraded" | "unknown";

interface ServiceHealth {
  name: string;
  label: string;
  status: ServiceStatus;
}

interface ServicesResponse {
  kafka?: { status: string };
  redis?: { status: string };
  postgres?: { status: string };
  qdrant?: { status: string };
  pipeline?: { status: string };
  [key: string]: { status: string } | undefined;
}

interface ModelsResponse {
  vlm?: { status: string };
  tts?: { status: string };
  lpr?: { status: string };
  [key: string]: { status: string } | undefined;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const POLL_INTERVAL_MS = 15_000;

const STATUS_COLORS: Record<ServiceStatus, string> = {
  healthy: "#22c55e",
  down: "#ef4444",
  degraded: "#f59e0b",
  unknown: "#6b7280",
};

const INFRA_KEYS: { key: string; label: string }[] = [
  { key: "kafka", label: "Kafka" },
  { key: "redis", label: "Redis" },
  { key: "postgres", label: "PostgreSQL" },
  { key: "qdrant", label: "Qdrant" },
  { key: "pipeline", label: "Pipeline" },
];

const MODEL_KEYS: { key: string; label: string }[] = [
  { key: "vlm", label: "VLM (Qwen2.5-VL)" },
  { key: "tts", label: "TTS (Piper)" },
  { key: "lpr", label: "LPR" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeStatus(raw?: string): ServiceStatus {
  if (!raw) return "unknown";
  const s = raw.toLowerCase();
  if (s === "healthy" || s === "ok" || s === "up" || s === "running") return "healthy";
  if (s === "degraded" || s === "warning") return "degraded";
  if (s === "down" || s === "error" || s === "unreachable") return "down";
  return "unknown";
}

function parseServices(
  data: ServicesResponse | null,
  keys: { key: string; label: string }[],
): ServiceHealth[] {
  return keys.map(({ key, label }) => ({
    name: key,
    label,
    status: data ? normalizeStatus(data[key]?.status) : "unknown",
  }));
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function HealthPanel() {
  const [expanded, setExpanded] = useState(false);
  const [infraHealth, setInfraHealth] = useState<ServiceHealth[]>(
    () => INFRA_KEYS.map(({ key, label }) => ({ name: key, label, status: "unknown" as ServiceStatus })),
  );
  const [modelHealth, setModelHealth] = useState<ServiceHealth[]>(
    () => MODEL_KEYS.map(({ key, label }) => ({ name: key, label, status: "unknown" as ServiceStatus })),
  );

  const fetchHealth = useCallback(async () => {
    try {
      const [svcRes, mdlRes] = await Promise.allSettled([
        fetch(`${API_BASE}/api/health/services`, { signal: AbortSignal.timeout(5000) }),
        fetch(`${API_BASE}/api/health/models`, { signal: AbortSignal.timeout(5000) }),
      ]);

      if (svcRes.status === "fulfilled" && svcRes.value.ok) {
        const data: ServicesResponse = await svcRes.value.json();
        setInfraHealth(parseServices(data, INFRA_KEYS));
      } else {
        setInfraHealth(INFRA_KEYS.map(({ key, label }) => ({ name: key, label, status: "unknown" })));
      }

      if (mdlRes.status === "fulfilled" && mdlRes.value.ok) {
        const data: ModelsResponse = await mdlRes.value.json();
        setModelHealth(parseServices(data, MODEL_KEYS));
      } else {
        setModelHealth(MODEL_KEYS.map(({ key, label }) => ({ name: key, label, status: "unknown" })));
      }
    } catch {
      // Network-level failure — mark everything unknown
      setInfraHealth(INFRA_KEYS.map(({ key, label }) => ({ name: key, label, status: "unknown" })));
      setModelHealth(MODEL_KEYS.map(({ key, label }) => ({ name: key, label, status: "unknown" })));
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchHealth]);

  // --- Derived counts ---
  const all = [...infraHealth, ...modelHealth];
  const total = all.length;
  const healthy = all.filter((s) => s.status === "healthy").length;
  const hasIssue = healthy < total;

  // --- Styles ---
  const pillStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 10px",
    borderRadius: 8,
    background: "rgba(255,255,255,0.05)",
    border: "1px solid rgba(255,255,255,0.1)",
    cursor: "pointer",
    fontSize: 11,
    fontFamily: "monospace",
    color: hasIssue ? STATUS_COLORS.degraded : STATUS_COLORS.healthy,
    userSelect: "none",
    transition: "background 0.15s",
  };

  const gridStyle: React.CSSProperties = {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "4px 12px",
    padding: "8px 10px",
    background: "rgba(0,0,0,0.2)",
    borderRadius: 8,
    marginTop: 6,
  };

  const dotStyle = (status: ServiceStatus): React.CSSProperties => ({
    width: 8,
    height: 8,
    borderRadius: "50%",
    backgroundColor: STATUS_COLORS[status],
    flexShrink: 0,
    boxShadow: status === "healthy" ? `0 0 4px ${STATUS_COLORS.healthy}40` : undefined,
  });

  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: 11,
    color: "rgba(255,255,255,0.75)",
    lineHeight: "18px",
  };

  const sectionLabelStyle: React.CSSProperties = {
    fontSize: 9,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    color: "rgba(255,255,255,0.4)",
    gridColumn: "1 / -1",
    marginTop: 4,
  };

  return (
    <div style={{ width: "100%" }}>
      {/* Collapsed pill */}
      <div
        style={pillStyle}
        onClick={() => setExpanded((e) => !e)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setExpanded((v) => !v); }}
        title="Toggle health details"
      >
        <span
          style={{
            ...dotStyle(hasIssue ? "degraded" : "healthy"),
            animation: hasIssue ? "healthPulse 2s ease-in-out infinite" : undefined,
          }}
        />
        <span>
          Services: {healthy}/{total} {hasIssue ? "\u26A0" : "\u2713"}
        </span>
        <span style={{ marginLeft: "auto", opacity: 0.5, fontSize: 10 }}>
          {expanded ? "\u25B2" : "\u25BC"}
        </span>
      </div>

      {/* Expanded grid */}
      {expanded && (
        <div style={gridStyle}>
          <div style={sectionLabelStyle}>Infrastructure</div>
          {infraHealth.map((svc) => (
            <div key={svc.name} style={rowStyle}>
              <span style={dotStyle(svc.status)} />
              <span>{svc.label}</span>
            </div>
          ))}
          <div style={{ ...sectionLabelStyle, marginTop: 8 }}>Models</div>
          {modelHealth.map((svc) => (
            <div key={svc.name} style={rowStyle}>
              <span style={dotStyle(svc.status)} />
              <span>{svc.label}</span>
            </div>
          ))}
        </div>
      )}

      {/* Pulse animation (injected once) */}
      <style>{`
        @keyframes healthPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}
