import { Metric, MetricRow } from "@/components/ui";

/**
 * What a teaching dataset adds up to: the same seven numbers, in the same
 * order, with the same words, wherever they are shown.
 *
 * They used to differ by screen. The dashboard counted the file it had just
 * read and showed classes with periods as a footnote; the Generate page
 * counted the selected dataset, had no classes at all, and called its faculty
 * figure "Faculty" while counting every teacher in the database qualified for
 * a subject - including teachers from other uploads. Now both pass the same
 * shape here.
 */
export type Figures = {
  sections: number;
  faculty: number;
  subjects: number;
  classes: number;
  requiredPeriods: number;
  rooms: number;
  labs: number;
};

export function SummaryFigures({ figures }: { figures: Figures }) {
  return (
    <div>
      <MetricRow columns={7}>
        <Metric label="Sections" value={figures.sections} />
        <Metric label="Faculty" value={figures.faculty} />
        <Metric label="Subjects" value={figures.subjects} />
        <Metric label="Classes" value={figures.classes} />
        <Metric label="Required periods" value={figures.requiredPeriods} />
        <Metric label="Rooms" value={figures.rooms} />
        <Metric label="Labs" value={figures.labs} />
      </MetricRow>
      <p className="mt-3 text-xs text-ink-muted">
        Classes are sessions a week - a two-period lab is one class. Required periods are
        those classes times their length.
      </p>
    </div>
  );
}
