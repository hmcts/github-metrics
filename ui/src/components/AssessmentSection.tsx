import { EmptyState } from '@/components/EmptyState';
import { RAGRow } from '@/components/RAGCard';
import { Section } from '@/components/Section';
import { conditionGroups } from '@/lib/repository';
import { conditionTone } from '@/lib/tone';
import type { ReadinessAssessment } from '@/lib/types';

/**
 * Everything the readiness policy checked, in the three groups it decided in.
 *
 * All three groups are shown, `clear` included, so a green label is as auditable as a red one: the
 * page states what was examined rather than only what failed, which is the difference between a
 * grade a team can argue with and one they can only be told.
 *
 * Only a blocking condition carries a label of its own — it is the ceiling that condition imposed —
 * so a caution and a clear row carry no badge beside their own sentence. What they carry instead,
 * since 2026-09-02, is the colour bar `tone.conditionTone` gives them: amber for a caution, green
 * for a condition that was checked and satisfied, and nothing at all for one the policy reported
 * without judging. That last group is why the flag exists — a `clear` section where a rule nobody
 * grades looks exactly like a check that passed reads as approval the policy never gave.
 */
export function AssessmentSection({ assessment }: { assessment: ReadinessAssessment }) {
  return (
    <div className="space-y-6">
      {conditionGroups(assessment).map((group) => (
        <Section key={group.key} heading={group.heading} detail={group.detail}>
          {group.conditions.length === 0 ? (
            <EmptyState message={group.empty} />
          ) : (
            <div className="space-y-2">
              {group.conditions.map((condition) => (
                <RAGRow
                  key={condition.condition}
                  label={condition.label}
                  tone={conditionTone(group.key, condition)}
                  condition={condition.condition}
                  detail={condition.detail}
                />
              ))}
            </div>
          )}
        </Section>
      ))}
    </div>
  );
}
