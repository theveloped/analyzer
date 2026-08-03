import { Disc3 } from 'lucide-react';
import { turningSplitHost } from '../../../processes/cnc/turning';
import { SplitControls } from '../../../splits/SplitControls';
import { useStore } from '../../../state/store';
import { Rail, RailHeader, RailStats } from '../../components/rail';
import { PickHint } from './shared';

/**
 * Turned-state roles. The only thing to configure is the face-split editor —
 * a face that is part turned and part not needs a cut before it can be given
 * one role, which is what `turning`'s CONFLICT_ROLE marks on the model.
 *
 * A rail rather than a declaration for that reason alone: splitting is a
 * two-click viewer interaction, not a param.
 */
export function TurningRail() {
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  return (
    <Rail>
      <RailHeader
        icon={Disc3}
        title="Turned state"
        blurb="Per-face turning roles for the best-fit axis."
      />
      <PickHint>
        Faces flagged as part-turned need a cut before they can take one role.
      </PickHint>
      <SplitControls host={turningSplitHost} />
      <RailStats text={stats} error={error} />
    </Rail>
  );
}
