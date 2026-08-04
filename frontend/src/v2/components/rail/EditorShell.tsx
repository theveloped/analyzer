import { Save } from 'lucide-react';
import type { ReactNode } from 'react';
import { Button } from '../../../catalyst/button';
import { Rail, RailHeader } from './Rail';

/**
 * The rail's sibling shell, for a surface that AUTHORS rather than inspects.
 *
 * `Rail` assumes the panel describes something the viewer already painted, so
 * its identity slot carries a status badge and its actions are incidental. An
 * editor inverts that: it owns unsaved state, and "save it / put it down" is
 * the most important thing on screen, not a trailing affordance. So the
 * actions region is explicit and always present, and the dirty flag drives it.
 *
 * Everything below the header is the same primitive set — one vocabulary, two
 * shells. The alternative was bending `Rail` until it covered both, which is
 * how a component ends up with a prop per word on screen.
 */
export function RailEditorShell({
  title, blurb, dirty, saving, onSave, onDone,
  doneLabel = 'Done', children,
}: {
  title: string;
  blurb?: ReactNode;
  /** Unsaved changes — enables Save and is the reason this shell exists. */
  dirty: boolean;
  saving?: boolean;
  onSave: () => void;
  onDone: () => void;
  doneLabel?: string;
  children: ReactNode;
}) {
  return (
    <Rail>
      <RailHeader
        title={title}
        blurb={blurb}
        actions={(
          <>
            {/* solid, not plain: on an authoring surface the save IS the
                point, and it has to outrank the way out of the editor */}
            <Button onClick={onSave} disabled={!dirty || saving}>
              <Save data-slot="icon" /> {saving ? 'Saving…' : 'Save'}
            </Button>
            <Button plain onClick={onDone}>{doneLabel}</Button>
          </>
        )}
      />
      {children}
    </Rail>
  );
}
