// The rail vocabulary. One import for every panel in the workspace — the right
// column's twelve surfaces and the left pipeline, which needs the section and
// error pieces too.
//
// See Rail.tsx for the seven slots and why they are in that order.

export { Rail, RailHeader } from './Rail';
export { RailSection, RailDivider } from './Section';
export { RailAlert, RailError, RailStats } from './Feedback';
export { RailDisclosure } from './Disclosure';
export { RailRunButton } from './RunButton';
export {
  RailBool, RailCheckbox, RailField, RailSegmented,
} from './fields';
