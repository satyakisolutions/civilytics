# Findings: dashboard violation highlighting

Status: bugs confirmed by reading the code. Not fixed. One decision open.
Split out of the camera onboarding plan; unrelated to onboarding.

## Requirement (learner's)
Camera panels on the landing page dashboard should highlight on violation. They
currently only turn blue on hover.

## Root cause
The violation styling is already written and correct. It is being suppressed.

`CameraFeed.tsx:185` applies `border-transparent` unconditionally as a Tailwind
utility. The red border comes from `.camera-feed.alert { @apply border-destructive }`
at `index.css:153`, inside `@layer components`. Utilities beat components in
Tailwind's cascade, so the transparent border always wins and the red border can
never render.

The blue seen on hover is `box-shadow: var(--glow-primary)` at `index.css:144` --
a raw property, not a utility, so nothing overrides it. That is why one signal
shows and the other does not.

## Second, smaller conflict
The hover rule guards with `:not(.animate-alert-border)` (`index.css:142`) while
the red state is class `.alert`. Both classes are applied together at
`CameraFeed.tsx:186-187`, so the guard works by accident rather than by design.

## Open decision
Alert and hover both want the panel outline. When an operator hovers a camera that
is actively in violation, what should they see -- red held, red plus a separate
hover affordance, or something else? And should a just-ended violation keep the
highlight briefly? An `alertHeldUntil` hold already exists at `CameraFeed.tsx:180`.
