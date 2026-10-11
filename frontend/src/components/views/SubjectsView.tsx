import { useState } from 'react';
import { Users } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useMonitoring } from '@/context/MonitoringContext';
import { ReviewQueue } from '@/components/subjects/ReviewQueue';
import { SubjectDetailPanel } from '@/components/subjects/SubjectDetailPanel';
import { SubjectGrid } from '@/components/subjects/SubjectGrid';

export function SubjectsView() {
  const { selectedSubjectId, openSubject } = useMonitoring();
  const [tab, setTab] = useState<'subjects' | 'review'>('subjects');
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-4">
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <Users className="w-5 h-5" /> Subjects
        </h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Anonymous IDs for people involved in violations. Only faces captured at a violation are kept, and a
          subject is deleted automatically after the retention period (90 days by default) without a new
          incident. Uncertain matches are never linked without your review.
        </p>
      </div>
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'subjects' | 'review')}>
        <TabsList>
          <TabsTrigger value="subjects">Subjects</TabsTrigger>
          <TabsTrigger value="review">Review</TabsTrigger>
        </TabsList>
        <TabsContent value="subjects" className="mt-4">
          {selectedSubjectId ? (
            <SubjectDetailPanel
              key={`${selectedSubjectId}-${refreshKey}`}
              subjectId={selectedSubjectId}
              onBack={() => openSubject(null)}
              onOpen={openSubject}
            />
          ) : (
            <SubjectGrid key={refreshKey} onOpen={openSubject} />
          )}
        </TabsContent>
        <TabsContent value="review" className="mt-4">
          <ReviewQueue onChanged={() => setRefreshKey((k) => k + 1)} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
