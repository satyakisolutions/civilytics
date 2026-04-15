import { forwardRef } from "react";
import { cn } from "@/lib/utils";

/**
 * Transcoded stream quality tiers published per camera by the LSG
 * RTSP streamer. The mediamtx server hosts one path per tier; the
 * DeepStream pipeline's output branches also mirror this suffix
 * convention.
 *
 * Path convention (relative to `rtsp://mediamtx:8554/`):
 *   large  -> "<camId>"       native resolution, highest bitrate
 *   medium -> "<camId>-md"    half-resolution, medium bitrate
 *   small  -> "<camId>-sm"    quarter-resolution, lowest bitrate
 *
 * The same suffix applies to the `-out` branches served back to the
 * browser after inference (e.g. "cam-01-md-out").
 */
export type StreamQuality = "small" | "medium" | "large";
export type StreamQualitySelection = StreamQuality | "auto";

export const STREAM_QUALITIES: StreamQuality[] = ["small", "medium", "large"];
export const STREAM_QUALITY_SELECTIONS: StreamQualitySelection[] = [
  "auto",
  "small",
  "medium",
  "large",
];

export interface StreamQualityMeta {
  id: StreamQuality;
  shortLabel: string;
  fullLabel: string;
  resolution: string;
  bitrateKbps: number;
  fps: number;
  description: string;
}

export const STREAM_QUALITY_META: Record<StreamQuality, StreamQualityMeta> = {
  small: {
    id: "small",
    shortLabel: "S",
    fullLabel: "Small",
    resolution: "426 x 240",
    bitrateKbps: 500,
    fps: 15,
    description: "Low bandwidth — ideal for mobile, multi-stream grids, or remote sites.",
  },
  medium: {
    id: "medium",
    shortLabel: "M",
    fullLabel: "Medium",
    resolution: "854 x 480",
    bitrateKbps: 1200,
    fps: 20,
    description: "Balanced quality — default for the live monitoring wall.",
  },
  large: {
    id: "large",
    shortLabel: "L",
    fullLabel: "Large",
    resolution: "1280 x 720",
    bitrateKbps: 3500,
    fps: 25,
    description: "Native HD — use for incident review and fullscreen playback.",
  },
};

/** Convert a stream path to the transcoded variant path. */
export function withQualitySuffix(streamPath: string, quality: StreamQuality): string {
  if (quality === "large") return streamPath;
  const suffix = quality === "small" ? "-sm" : "-md";
  // If the path already carries a known tier suffix, swap it.
  if (streamPath.endsWith("-sm") || streamPath.endsWith("-md")) {
    return streamPath.replace(/-(sm|md)$/, suffix);
  }
  return `${streamPath}${suffix}`;
}

export interface StreamQualityToggleProps {
  /** Current user selection. "auto" means "follow the panel size". */
  value: StreamQualitySelection;
  onChange: (q: StreamQualitySelection) => void;
  /**
   * Effective quality currently in use. Relevant when `value === "auto"`
   * so the auto button can display the tier it's resolving to.
   */
  autoQuality?: StreamQuality;
  className?: string;
  size?: "xs" | "sm" | "md";
  disabled?: boolean;
  variant?: "pill" | "ghost" | "solid";
  compact?: boolean;
}

/** Resolve the effective quality given a selection + the auto tier. */
export function resolveStreamQuality(
  selection: StreamQualitySelection,
  autoQuality: StreamQuality
): StreamQuality {
  return selection === "auto" ? autoQuality : selection;
}

/**
 * Segmented three-button toggle for swapping between the S / M / L
 * transcoded variants of a camera stream. Drop into any panel header
 * that owns a `useState<StreamQuality>` and a matching stream URL
 * derivation. Rendered as a single control so it fits snugly next to
 * other player chrome (fullscreen, pin, audio toggle).
 */
export const StreamQualityToggle = forwardRef<HTMLDivElement, StreamQualityToggleProps>(
  (
    {
      value,
      onChange,
      autoQuality,
      className,
      size = "sm",
      disabled,
      variant = "pill",
      compact = false,
    },
    ref
  ) => {
    const sizeClasses = {
      xs: "h-5 text-[10px]",
      sm: "h-6 text-[11px]",
      md: "h-7 text-xs",
    }[size];

    const buttonBase =
      "inline-flex items-center justify-center font-semibold tracking-wide transition-colors " +
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-40";

    const variantWrapper = {
      pill: "rounded-full border border-white/15 bg-black/50 backdrop-blur-sm px-0.5 py-0.5",
      ghost: "rounded-md border border-border bg-background/80 px-0.5 py-0.5",
      solid: "rounded-md bg-muted px-0.5 py-0.5",
    }[variant];

    const renderButton = (selection: StreamQualitySelection, index: number) => {
      const active = value === selection;
      let label: string;
      let titleText: string;
      let ariaLabel: string;

      if (selection === "auto") {
        const resolved = autoQuality ?? "medium";
        const meta = STREAM_QUALITY_META[resolved];
        label = "A";
        titleText = `Auto · follows panel size\nCurrently: ${meta.fullLabel} (${meta.resolution} · ${meta.bitrateKbps} kbps)`;
        ariaLabel = `Auto quality — currently ${meta.fullLabel} at ${meta.resolution}`;
      } else {
        const meta = STREAM_QUALITY_META[selection];
        label = meta.shortLabel;
        titleText = `${meta.fullLabel} · ${meta.resolution} · ${meta.bitrateKbps} kbps · ${meta.fps} fps\n${meta.description}`;
        ariaLabel = `${meta.fullLabel} — ${meta.resolution}, ${meta.bitrateKbps} kbps`;
      }

      return (
        <button
          key={selection}
          type="button"
          role="radio"
          aria-checked={active}
          aria-label={ariaLabel}
          title={titleText}
          onClick={() => !disabled && onChange(selection)}
          disabled={disabled}
          data-index={index}
          className={cn(
            buttonBase,
            sizeClasses,
            compact ? "px-1.5" : "px-2",
            "rounded-full",
            active
              ? "bg-primary text-primary-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground hover:bg-white/5",
            // Subtle divider between Auto and the manual tiers.
            selection === "auto" && "border-r border-white/10 mr-0.5 pr-2"
          )}
        >
          {label}
        </button>
      );
    };

    return (
      <div
        ref={ref}
        role="radiogroup"
        aria-label="Stream quality"
        className={cn("inline-flex items-center gap-0.5", variantWrapper, className)}
      >
        {STREAM_QUALITY_SELECTIONS.map((selection, i) => renderButton(selection, i))}
      </div>
    );
  }
);

StreamQualityToggle.displayName = "StreamQualityToggle";
