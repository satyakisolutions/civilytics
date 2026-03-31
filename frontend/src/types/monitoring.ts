// Core types for the Smart Waste Monitoring System

export type CameraStatus = 'online' | 'offline' | 'warning' | 'alert';
export type AlertSeverity = 'critical' | 'warning' | 'info';
export type AlertStatus = 'active' | 'acknowledged' | 'resolved';
export type GridLayout = '2x2' | '3x3' | '4x4' | '1x1';
export type UserRole = 'viewer' | 'admin' | 'auditor';

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

export interface BBoxDetection {
  class_id: number;
  class_name: string;
  confidence: number;
  bbox: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0-1
  track_id: number | null;
}

export interface FrameMetadata {
  camera_id: string;
  frame_number: number;
  timestamp: number;
  detections: BBoxDetection[];
  fps?: number;
}
