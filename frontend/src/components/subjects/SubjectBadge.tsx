import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { subjectBadgeInfo, type BadgeTone, type SubjectFields } from '@/lib/subjects';

const TONE_CLASS: Record<BadgeTone, string> = {
  new: 'bg-primary/10 text-primary border-primary/30',
  repeat: 'bg-destructive/15 text-destructive border-destructive/40 font-semibold',
  review: 'bg-amber-500/15 text-amber-600 border-amber-500/40',
  muted: 'bg-muted text-muted-foreground border-border',
};

interface SubjectBadgeProps {
  fields: SubjectFields;
  onOpen?: (subjectId: string) => void;
  className?: string;
}

export function SubjectBadge({ fields, onOpen, className }: SubjectBadgeProps) {
  const info = subjectBadgeInfo(fields);
  if (!info) return null;
  const subjectId = info.subjectId;
  const clickable = Boolean(subjectId && onOpen);
  return (
    <Badge
      variant="outline"
      data-testid="subject-badge"
      role={clickable ? 'button' : undefined}
      onClick={clickable ? () => onOpen!(subjectId!) : undefined}
      className={cn(
        'text-[10px] px-1.5 h-5 whitespace-nowrap',
        TONE_CLASS[info.tone],
        clickable && 'cursor-pointer hover:opacity-80',
        className,
      )}
    >
      {info.label}
    </Badge>
  );
}
