-- Create enum for camera status
CREATE TYPE public.camera_status AS ENUM ('online', 'offline', 'warning', 'alert');

-- Create enum for alert severity
CREATE TYPE public.alert_severity AS ENUM ('critical', 'warning', 'info');

-- Create enum for alert status
CREATE TYPE public.alert_status AS ENUM ('active', 'acknowledged', 'resolved');

-- Create enum for event type
CREATE TYPE public.event_type AS ENUM ('dumping_detected', 'suspicious_activity', 'repeat_offender');

-- Create cameras table
CREATE TABLE public.cameras (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  zone TEXT NOT NULL,
  location TEXT NOT NULL,
  gps_lat DECIMAL(10, 8),
  gps_lng DECIMAL(11, 8),
  status camera_status NOT NULL DEFAULT 'offline',
  stream_url TEXT,
  thumbnail_url TEXT,
  is_pinned BOOLEAN DEFAULT false,
  last_event_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create violation_events table
CREATE TABLE public.violation_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  camera_id UUID REFERENCES public.cameras(id) ON DELETE CASCADE NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
  event_type event_type NOT NULL,
  severity alert_severity NOT NULL DEFAULT 'warning',
  confidence INTEGER CHECK (confidence >= 0 AND confidence <= 100),
  person_id TEXT,
  repeat_count INTEGER DEFAULT 1,
  snapshot_url TEXT,
  video_clip_url TEXT,
  status alert_status NOT NULL DEFAULT 'active',
  acknowledged_by TEXT,
  acknowledged_at TIMESTAMPTZ,
  notes TEXT,
  is_valid BOOLEAN,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create alerts table
CREATE TABLE public.alerts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id UUID REFERENCES public.violation_events(id) ON DELETE CASCADE,
  severity alert_severity NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
  status alert_status NOT NULL DEFAULT 'active',
  camera_id UUID REFERENCES public.cameras(id) ON DELETE CASCADE NOT NULL,
  zone TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create dropbox_tokens table for OAuth
CREATE TABLE public.dropbox_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  access_token TEXT NOT NULL,
  refresh_token TEXT,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Create evidence_files table to track synced files
CREATE TABLE public.evidence_files (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id UUID REFERENCES public.violation_events(id) ON DELETE CASCADE NOT NULL,
  file_type TEXT NOT NULL,
  local_path TEXT,
  dropbox_path TEXT,
  sync_status TEXT DEFAULT 'pending',
  synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Enable Row Level Security
ALTER TABLE public.cameras ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.violation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dropbox_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.evidence_files ENABLE ROW LEVEL SECURITY;

-- RLS Policies - Allow public read for monitoring app (no auth required for MVP)
CREATE POLICY "Allow public read on cameras" ON public.cameras FOR SELECT USING (true);
CREATE POLICY "Allow public insert on cameras" ON public.cameras FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on cameras" ON public.cameras FOR UPDATE USING (true);

CREATE POLICY "Allow public read on violation_events" ON public.violation_events FOR SELECT USING (true);
CREATE POLICY "Allow public insert on violation_events" ON public.violation_events FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on violation_events" ON public.violation_events FOR UPDATE USING (true);

CREATE POLICY "Allow public read on alerts" ON public.alerts FOR SELECT USING (true);
CREATE POLICY "Allow public insert on alerts" ON public.alerts FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on alerts" ON public.alerts FOR UPDATE USING (true);

CREATE POLICY "Allow public read on dropbox_tokens" ON public.dropbox_tokens FOR SELECT USING (true);
CREATE POLICY "Allow public insert on dropbox_tokens" ON public.dropbox_tokens FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on dropbox_tokens" ON public.dropbox_tokens FOR UPDATE USING (true);
CREATE POLICY "Allow public delete on dropbox_tokens" ON public.dropbox_tokens FOR DELETE USING (true);

CREATE POLICY "Allow public read on evidence_files" ON public.evidence_files FOR SELECT USING (true);
CREATE POLICY "Allow public insert on evidence_files" ON public.evidence_files FOR INSERT WITH CHECK (true);
CREATE POLICY "Allow public update on evidence_files" ON public.evidence_files FOR UPDATE USING (true);

-- Create storage bucket for evidence
INSERT INTO storage.buckets (id, name, public) VALUES ('evidence', 'evidence', true);

-- Storage policies for evidence bucket
CREATE POLICY "Allow public read on evidence" ON storage.objects FOR SELECT USING (bucket_id = 'evidence');
CREATE POLICY "Allow public upload to evidence" ON storage.objects FOR INSERT WITH CHECK (bucket_id = 'evidence');

-- Create function to update timestamps
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = public;

-- Create triggers for timestamp updates
CREATE TRIGGER update_cameras_updated_at
  BEFORE UPDATE ON public.cameras
  FOR EACH ROW
  EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER update_dropbox_tokens_updated_at
  BEFORE UPDATE ON public.dropbox_tokens
  FOR EACH ROW
  EXECUTE FUNCTION public.update_updated_at_column();

-- Enable realtime for alerts
ALTER PUBLICATION supabase_realtime ADD TABLE public.alerts;
ALTER PUBLICATION supabase_realtime ADD TABLE public.violation_events;