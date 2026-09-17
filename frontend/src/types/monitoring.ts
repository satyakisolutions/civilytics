// Core types for the Smart Waste Monitoring System

export type CameraStatus = 'online' | 'offline' | 'warning' | 'alert';
export type AlertSeverity = 'critical' | 'warning' | 'info';
export type AlertStatus = 'active' | 'acknowledged' | 'resolved';
export type GridLayout = '2x2' | '3x3' | '4x4' | '1x1';
export type UserRole = 'viewer' | 'admin' | 'auditor';

export interface CameraDetectionConfig {
  dwell_enter_threshold_s?: number;
  dwell_violation_threshold_s?: number;
  waste_proximity_px?: number;
  bag_proximity_px?: number;
}

export interface Camera {
  id: string;
  name: string;
  zone: string;
  location: string;
  gps?: { lat: number; lng: number };
  status: CameraStatus;
  streamUrl?: string;
  thumbnail?: string;
  lastEvent?: Date;
  isPinned?: boolean;
  detectionConfig?: CameraDetectionConfig;
}

export interface Zone {
  id: string;
  name: string;
  location: string;
  cameraCount: number;
  activeAlerts: number;
}

export interface ViolationEvent {
  id: string;
  cameraId: string;
  cameraName: string;
  zone: string;
  location: string;
  timestamp: Date;
  eventType: 'dumping_detected' | 'suspicious_activity' | 'repeat_offender';
  severity: AlertSeverity;
  confidence: number;
  personId?: string;
  repeatCount?: number;
  snapshotUrl?: string;
  videoClipUrl?: string;
  status: AlertStatus;
  acknowledgedBy?: string;
  acknowledgedAt?: Date;
  notes?: string;
  isValid?: boolean;
}

export interface Alert {
  id: string;
  eventId: string;
  severity: AlertSeverity;
  title: string;
  message: string;
  timestamp: Date;
  status: AlertStatus;
  cameraId: string;
  zone: string;
  ttsAudioUrl?: string;
}

export interface AnalyticsData {
  totalEvents: number;
  eventsToday: number;
  eventsThisWeek: number;
  eventsThisMonth: number;
  repeatOffenders: number;
  resolvedEvents: number;
  pendingEvents: number;
  averageResponseTime: number;
  cameraUptime: number;
  topZones: { zone: string; count: number }[];
  hourlyDistribution: { hour: number; count: number }[];
  dailyTrend: { date: string; count: number }[];
}

export interface SystemStatus {
  camerasOnline: number;
  camerasTotal: number;
  activeAlerts: number;
  systemHealth: 'healthy' | 'degraded' | 'critical';
  lastSync: Date;
  storageUsed: number;
  storageTotal: number;
}

// --- Phase 1: Real-time detection metadata from backend WebSocket ---

export type EventFSMState = 'idle' | 'enter' | 'observe' | 'violation' | 'alerting' | 'escalated' | 'exited';

export interface VLMResult {
  is_dumping: boolean;
  waste_type?: string;
  confidence: number;
  reasoning?: string;
}

export interface BBoxDetection {
  class_id: number;
  class_name: string;
  confidence: number;
  bbox: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0-1
  track_id: number | null;
  dwell_time_s?: number;
  in_roi?: boolean;
  fsm_state?: EventFSMState;
  // Action classification (from SGIE)
  action_label?: string;
  action_confidence?: number;
  action_threat?: string;
  action_fast_alert?: boolean;
  // Object category
  category?: 'person' | 'vehicle' | 'carried_item' | 'waste_object';
  // Evidence
  plate_number?: string;
  vlm_result?: VLMResult;
  recording?: boolean;
  // Alert
  alert_stage?: string;
  alert_priority?: string;
}

export type CameraAlertLevel = 'none' | 'monitoring' | 'suspicious' | 'violation' | 'critical';

export interface CameraAlertFlag {
  level: CameraAlertLevel;
  text: string;             // Custom per-camera alert text shown on frame
  color: string;            // Hex color for the banner
  since?: number;           // timestamp when alert started
}

export interface FrameMetadata {
  camera_id: string;
  frame_number: number;
  timestamp: number;
  detections: BBoxDetection[];
  fps?: number;
  camera_alert?: CameraAlertFlag;  // Per-camera alert banner
}

// --- Phase 2: Tracking + ROI types ---

export interface ROIPoint {
  x: number;
  y: number;
}

export interface ROIPolygon {
  name: string;
  points: ROIPoint[];
}

export interface TrackSession {
  id: string;
  camera_id: string;
  track_id: number;
  fsm_state: EventFSMState;
  entered_at: string;
  exited_at: string | null;
  dwell_time_s: number | null;
}
