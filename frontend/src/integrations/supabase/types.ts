export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.1"
  }
  public: {
    Tables: {
      alerts: {
        Row: {
          camera_id: string
          created_at: string
          event_id: string | null
          id: string
          message: string
          severity: Database["public"]["Enums"]["alert_severity"]
          status: Database["public"]["Enums"]["alert_status"]
          timestamp: string
          title: string
          zone: string
        }
        Insert: {
          camera_id: string
          created_at?: string
          event_id?: string | null
          id?: string
          message: string
          severity: Database["public"]["Enums"]["alert_severity"]
          status?: Database["public"]["Enums"]["alert_status"]
          timestamp?: string
          title: string
          zone: string
        }
        Update: {
          camera_id?: string
          created_at?: string
          event_id?: string | null
          id?: string
          message?: string
          severity?: Database["public"]["Enums"]["alert_severity"]
          status?: Database["public"]["Enums"]["alert_status"]
          timestamp?: string
          title?: string
          zone?: string
        }
        Relationships: [
          {
            foreignKeyName: "alerts_camera_id_fkey"
            columns: ["camera_id"]
            isOneToOne: false
            referencedRelation: "cameras"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "alerts_event_id_fkey"
            columns: ["event_id"]
            isOneToOne: false
            referencedRelation: "violation_events"
            referencedColumns: ["id"]
          },
        ]
      }
      cameras: {
        Row: {
          created_at: string
          gps_lat: number | null
          gps_lng: number | null
          id: string
          is_pinned: boolean | null
          last_event_at: string | null
          location: string
          name: string
          status: Database["public"]["Enums"]["camera_status"]
          stream_url: string | null
          thumbnail_url: string | null
          updated_at: string
          zone: string
        }
        Insert: {
          created_at?: string
          gps_lat?: number | null
          gps_lng?: number | null
          id?: string
          is_pinned?: boolean | null
          last_event_at?: string | null
          location: string
          name: string
          status?: Database["public"]["Enums"]["camera_status"]
          stream_url?: string | null
          thumbnail_url?: string | null
          updated_at?: string
          zone: string
        }
        Update: {
          created_at?: string
          gps_lat?: number | null
          gps_lng?: number | null
          id?: string
          is_pinned?: boolean | null
          last_event_at?: string | null
          location?: string
          name?: string
          status?: Database["public"]["Enums"]["camera_status"]
          stream_url?: string | null
          thumbnail_url?: string | null
          updated_at?: string
          zone?: string
        }
        Relationships: []
      }
      dropbox_tokens: {
        Row: {
          access_token: string
          created_at: string
          expires_at: string | null
          id: string
          refresh_token: string | null
          updated_at: string
        }
        Insert: {
          access_token: string
          created_at?: string
          expires_at?: string | null
          id?: string
          refresh_token?: string | null
          updated_at?: string
        }
        Update: {
          access_token?: string
          created_at?: string
          expires_at?: string | null
          id?: string
          refresh_token?: string | null
          updated_at?: string
        }
        Relationships: []
      }
      evidence_files: {
        Row: {
          created_at: string
          dropbox_path: string | null
          event_id: string
          file_type: string
          id: string
          local_path: string | null
          sync_status: string | null
          synced_at: string | null
        }
        Insert: {
          created_at?: string
          dropbox_path?: string | null
          event_id: string
          file_type: string
          id?: string
          local_path?: string | null
          sync_status?: string | null
          synced_at?: string | null
        }
        Update: {
          created_at?: string
          dropbox_path?: string | null
          event_id?: string
          file_type?: string
          id?: string
          local_path?: string | null
          sync_status?: string | null
          synced_at?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "evidence_files_event_id_fkey"
            columns: ["event_id"]
            isOneToOne: false
            referencedRelation: "violation_events"
            referencedColumns: ["id"]
          },
        ]
      }
      profiles: {
        Row: {
          created_at: string
          display_name: string | null
          email: string | null
          id: string
          updated_at: string
          user_id: string
        }
        Insert: {
          created_at?: string
          display_name?: string | null
          email?: string | null
          id?: string
          updated_at?: string
          user_id: string
        }
        Update: {
          created_at?: string
          display_name?: string | null
          email?: string | null
          id?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      user_roles: {
        Row: {
          created_at: string
          id: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Insert: {
          created_at?: string
          id?: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Update: {
          created_at?: string
          id?: string
          role?: Database["public"]["Enums"]["app_role"]
          user_id?: string
        }
        Relationships: []
      }
      violation_events: {
        Row: {
          acknowledged_at: string | null
          acknowledged_by: string | null
          camera_id: string
          confidence: number | null
          created_at: string
          event_type: Database["public"]["Enums"]["event_type"]
          id: string
          is_valid: boolean | null
          notes: string | null
          person_id: string | null
          repeat_count: number | null
          severity: Database["public"]["Enums"]["alert_severity"]
          snapshot_url: string | null
          status: Database["public"]["Enums"]["alert_status"]
          timestamp: string
          video_clip_url: string | null
        }
        Insert: {
          acknowledged_at?: string | null
          acknowledged_by?: string | null
          camera_id: string
          confidence?: number | null
          created_at?: string
          event_type: Database["public"]["Enums"]["event_type"]
          id?: string
          is_valid?: boolean | null
          notes?: string | null
          person_id?: string | null
          repeat_count?: number | null
          severity?: Database["public"]["Enums"]["alert_severity"]
          snapshot_url?: string | null
          status?: Database["public"]["Enums"]["alert_status"]
          timestamp?: string
          video_clip_url?: string | null
        }
        Update: {
          acknowledged_at?: string | null
          acknowledged_by?: string | null
          camera_id?: string
          confidence?: number | null
          created_at?: string
          event_type?: Database["public"]["Enums"]["event_type"]
          id?: string
          is_valid?: boolean | null
          notes?: string | null
          person_id?: string | null
          repeat_count?: number | null
          severity?: Database["public"]["Enums"]["alert_severity"]
          snapshot_url?: string | null
          status?: Database["public"]["Enums"]["alert_status"]
          timestamp?: string
          video_clip_url?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "violation_events_camera_id_fkey"
            columns: ["camera_id"]
            isOneToOne: false
            referencedRelation: "cameras"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      has_role: {
        Args: {
          _role: Database["public"]["Enums"]["app_role"]
          _user_id: string
        }
        Returns: boolean
      }
    }
    Enums: {
      alert_severity: "critical" | "warning" | "info"
      alert_status: "active" | "acknowledged" | "resolved"
      app_role: "viewer" | "admin" | "auditor"
      camera_status: "online" | "offline" | "warning" | "alert"
      event_type: "dumping_detected" | "suspicious_activity" | "repeat_offender"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {
      alert_severity: ["critical", "warning", "info"],
      alert_status: ["active", "acknowledged", "resolved"],
      app_role: ["viewer", "admin", "auditor"],
      camera_status: ["online", "offline", "warning", "alert"],
      event_type: [
        "dumping_detected",
        "suspicious_activity",
        "repeat_offender",
      ],
    },
  },
} as const
