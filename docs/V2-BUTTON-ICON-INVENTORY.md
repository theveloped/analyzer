# V2 button and icon inventory

Companion to [ANALYSIS-INVENTORY.md](ANALYSIS-INVENTORY.md), for reviewing every
current V2 button and icon in a design tool. This records the implementation as of
2026-07-29; it is not a recommendation that the current icon is the final one.
When this file and the UI disagree, the UI wins.

## 1. What actually becomes a button

The analysis inventory contains several layers, but they do not map one-to-one to
buttons:

- **Prep stages** are resolver-owned prerequisites. They should not become primary
  user buttons merely because they appear in the backend registry.
- **Analyses** own computation. They surface as Run/Re-run actions in a rail, as
  plan checks, or through a lens that materializes the analysis.
- **Lenses** are the current inspection-tool buttons. `v2/lenses.ts` builds **40
  runtime lenses**. Eight are duplicated as always-visible icon buttons; the
  searchable Wrench menu lists all 40 and hides five advanced lenses until
  Advanced mode is enabled.
- **Candidate directions** is an additional dedicated toolbar button backed by the
  hidden `directions:directions` mode. It is intentionally outside the 40-item
  runtime lens list.
- **Checks** are policy-bearing cards in the left pipeline, not additional viewer
  toolbar icons. Their icons come from `v2/analyses.ts` and
  `v2/checks/catalog.ts`.

The practical icon-review set is therefore: the 40 lens meanings below, the
candidate-directions button, viewport controls, navigation/actions, and plan/check
cards.

## 2. Current icon language

- **Library:** `lucide-react` 0.469.0. V2 imports Lucide components directly; there
  is no app-level icon wrapper or semantic icon token layer.
- **Construction:** 24 × 24 SVG view box, no fill, `currentColor` stroke, 2 px
  stroke, round line caps and joins.
- **Rendered sizes:** 16 px in the floating toolbars, 20 px in desktop sidebar
  rows, usually 14 px in rail headings/cards, and 16 px in Catalyst buttons.
- **Colour:** icons inherit the button state. Sidebar/Catalyst icons can instead be
  tinted through `data-slot="icon"`.
- **Non-Lucide glyphs:** `✕`/`×` are used for a few clear/remove actions. PMI uses
  ISO/Unicode GD&T symbols such as `⌖`, `⌀`, `Ⓜ`, `∥`, `◎`, and `⌯`; these are
  domain notation, not decorative icons.

### Custom-icon compatibility target

A replacement or new icon should be drawn on a 24 × 24 grid, use a 2 px outline,
round caps/joins, no fill, and remain legible at 16 × 16. Test it in four states:
neutral grey, neutral hover, white-on-dark active, and dark-on-white active. A
custom icon that needs colour or fine internal detail will not match the present
button system.

## 3. Current button styling

### 3.1 Floating analysis and viewport toolbars

The top analysis toolbar and bottom viewport toolbar deliberately share one visual
primitive, although it is duplicated as local class strings in two files.

| Property | Current implementation |
| --- | --- |
| Button box | 32 × 32 px |
| Corner radius | 8 px |
| Icon | 16 × 16 px Lucide outline |
| Border | none on each button |
| Idle, light | transparent; zinc-500 icon |
| Idle hover, light | 5% zinc-950 background; zinc-950 icon |
| Active, light | zinc-900 background; white icon |
| Idle/active, dark | zinc-400 on transparent; active is white background with zinc-900 icon |
| Transition | 150 ms colour/background transition |
| Group spacing | 4 px gap; 4 px padding |
| Group container | 12 px radius; translucent white/zinc-800 at 90%; 1 px low-contrast border + ring; large shadow; backdrop blur |
| Group separators | 1 px vertical rule, 20 px high |
| Labels | icon-only at rest; native `title` text on hover |
| Toggle state | `aria-pressed` plus the inverted active treatment |

The Wrench popover uses the same 32 px launcher. Inside it, a tool becomes a
full-width 14 px text row with a 14 px icon, 8 px radius, 8 px horizontal padding,
and the same dark/light active inversion. The popover is 288 px wide.

The opacity buttons are a special case: hover/focus expands a 60 px range slider
out of the 32 px button while preserving the icon's screen position.

### 3.2 Catalyst action buttons

Run, Re-run, Add, Publish, Apply, Cancel, and similar rail actions use
`catalyst/button.tsx`.

| Property | Current implementation |
| --- | --- |
| Desktop height | normally 36 px for one-line buttons |
| Corner radius | 8 px |
| Text | system UI, 14/24 px, weight 600 |
| Layout | centred inline-flex, 8 px icon/text gap, 11 px horizontal and 5 px vertical padding at desktop |
| Icon | 16 px when the SVG has `data-slot="icon"` |
| Solid default | zinc-900 foreground layer, white text, subtle optical border, small outer shadow and 1 px inset highlight |
| Outline | transparent, 1 px zinc/10 border; 2.5% neutral hover |
| Plain | transparent border; 5% neutral hover |
| Focus | 2 px blue-500 outline with 2 px offset |
| Disabled | 50% opacity; shadows removed |
| Touch | invisible target expands to at least 44 × 44 px on coarse pointers |

The primitive supports many colour variants, but current V2 primary actions mostly
use the default dark/zinc solid. Blue is primarily the focus colour and the active
check-card accent, not the default CTA fill.

### 3.3 Sidebar rows

`SidebarItem` is a full-width navigation button: 36 px high at the current desktop
viewport, 8 px padding, 12 px icon/text gap, 8 px radius, 14/20 px medium text, and
a 20 px icon. Hover adds a 5% neutral fill. The current item is identified by a
2 px vertical bar on the far left and a darker icon, not by a filled row.

The Add-part icon button, reprocess button, and upload drop zone are local variants,
not `SidebarItem`. The empty-state upload target is a 78 px-high dashed tile with a
20 px Upload icon.

### 3.4 Check cards and rail-local buttons

- Check cards are full-width, left-aligned 8 px-radius cards. Their icon is 14 px,
  paired with a status dot. Selection adds a blue-500/30 border and blue-500/5
  background.
- Disclosures use a 14 px leading concept icon plus a ChevronDown that rotates.
- Segmented controls and chips are text-first local buttons, generally 8 px radius
  (6 px for PMI chips), with neutral idle and dark active states.
- Delete/close affordances are low-emphasis 12–14 px X/Trash icons; some only appear
  on group hover.
- Legacy plugin controls are still rendered inside `.v1-controls`: hardcoded dark
  blue-grey 5 px-radius buttons, 12 px text, 5 × 10 px padding, and a green primary
  variant. This is intentionally a temporary visual seam, not part of the V2
  button language.

## 4. Lens buttons: one-by-one review sheet

`Pinned` means the lens has a permanent 32 px button in the top toolbar. Every row
also appears in the Wrench menu, subject to the Advanced filter. The final column
is deliberately left open for the design review.

### Model data

| Lens id | UI label | Current Lucide icon | Placement/state | Design decision |
| --- | --- | --- | --- | --- |
| `brep_faces` | BREP faces | `Shapes` | Pinned | — |
| `face_attrs` | STEP colors / names | `Palette` | Pinned | — |
| `pmi` | PMI / GD&T | `Frame` | Pinned | — |
| `highlights` | Last CLI highlights.json | `Highlighter` | Advanced menu | — |

### Geometry

| Lens id | UI label | Current Lucide icon | Placement/state | Design decision |
| --- | --- | --- | --- | --- |
| `thickness` | Wall thickness heatmap | `Ruler` | Pinned | — |
| `gaps` | Wall gaps / clearance heatmap | `Spline` | Pinned; also reused by CNC tip gap | — |
| `rayThickness` | Ray wall thickness heatmap | `Ratio` | Pinned | — |
| `rayGap` | Ray wall gap / clearance heatmap | `Radius` | Pinned; also reused by bend radius | — |
| `thinSpan` | Thin span / stiffness heatmap | `Waves` | Pinned | — |
| `thicknessAngle` | Thickness contact angle | `Compass` | Advanced menu; same icon as gap angle | — |
| `gapAngle` | Gap contact angle | `Compass` | Advanced menu; same icon as thickness angle | — |

### CNC

| Lens id | UI label | Current Lucide icon | Placement/state | Design decision |
| --- | --- | --- | --- | --- |
| `setups` | Setup assignment (3-axis / 3+2) | `Axis3d` | Menu; shared with operation reach | — |
| `features` | Machined features | `Drill` | Menu | — |
| `turning` | Turning roles | `Disc3` | Menu | — |
| `turning_residual` | Revolution error | `CircleDot` | Advanced menu | — |
| `hull` | Convex hull faces | `Box` | Menu | — |
| `reach_study` | Reach study (direction × tool) | `Eye` | Menu; shared with accessibility | — |
| `reach_op` | Operation reach (any tool in cone) | `Axis3d` | Menu; shared with setups | — |
| `reach_aggregate` | Route reach (all operations) | `ShieldCheck` | Menu; shared with unified verdict | — |
| `unified` | Unified verdict (tool + holder) | `ShieldCheck` | Menu; legacy path | — |
| `access` | Accessibility (undercuts) | `Eye` | Menu | — |
| `class` | Surface class (normal vs direction) | `Layers` | Menu; heavily reused icon | — |
| `gap` | Tip gap heatmap | `Spline` | Menu | — |
| `stickout` | Required stickout heatmap | `MoveVertical` | Menu | — |
| `thinSpan` | Thin span / stiffness heatmap | `Waves` | Menu; duplicate of Geometry lens | — |

### Injection molding

| Lens id | UI label | Current Lucide icon | Placement/state | Design decision |
| --- | --- | --- | --- | --- |
| `assignment` | Mold orientation assignment | `Layers` | Menu; heavily reused icon | — |
| `sprue` | Sprue proposals | `Pin` | Menu | — |
| `flowFill` | Flow fill (voxel) | `Droplets` | Menu | — |
| `cooling` | Cooling time | `Snowflake` | Menu | — |
| `ejector` | Ejector pins | `ArrowUpFromLine` | Menu | — |
| `slenderness` | Steel slenderness heatmap | `TrendingUp` | Menu | — |
| `skeleton` | Skeleton & fill flow | `Network` | Menu | — |
| `voxelField` | Voxel fields (debug) | `Grid3x3` | Advanced menu | — |

### Sheet metal

| Lens id | UI label | Current Lucide icon | Placement/state | Design decision |
| --- | --- | --- | --- | --- |
| `flat_pattern` | Flat pattern | `Expand` | Menu | — |
| `bend_plan` | Bend plan (press brake) | `ListOrdered` | Menu | — |
| `bend_sequence` | Bend sequence (animate) | `Play` | Menu | — |
| `sheet_roles` | Sheet face roles | `Layers` | Menu; heavily reused icon | — |
| `bend_radius` | Bend radius | `Radius` | Menu | — |

### Tube / profile laser

| Lens id | UI label | Current Lucide icon | Placement/state | Design decision |
| --- | --- | --- | --- | --- |
| `tube_roles` | Shell roles | `Layers` | Menu; heavily reused icon | — |
| `cut_pattern` | Cut pattern (unrolled) | `Scissors` | Menu | — |

### Icon reuse inside the 40 lenses

The 40 runtime lenses currently use 30 distinct Lucide icons. Reuse is:

- `Layers`: 4 meanings — mold assignment, CNC surface class, sheet roles, tube roles.
- `Spline`: 2 — wall clearance and CNC tip gap.
- `Radius`: 2 — ray gap and sheet bend radius.
- `Waves`: 2 — the duplicated thin-span lenses.
- `Compass`: 2 — thickness angle and gap angle.
- `Axis3d`: 2 — setup assignment and operation reach.
- `ShieldCheck`: 2 — unified verdict and route reach.
- `Eye`: 2 — accessibility and reach study.

Some reuse is a useful family relationship; some produces indistinguishable
neighbours. The design pass should decide that explicitly rather than enforcing
uniqueness mechanically.

## 5. Dedicated viewer and utility buttons

### Top analysis toolbar

| Action | Current icon | Current form | Design decision |
| --- | --- | --- | --- |
| All inspection tools | `Wrench` | 32 px popover launcher | — |
| Candidate directions | `Crosshair` | 32 px toggle; dedicated hidden-mode button | — |
| Show/hide advanced tools | `MoreHorizontal` | 32 px toggle | — |
| Search all tools | `Search` | 14 px input decoration, not a button | — |

### Bottom viewport toolbar

| Action | Current icon | Behaviour | Design decision |
| --- | --- | --- | --- |
| Solid render | `Circle` | Exclusive render-style toggle | — |
| Mesh render | `Triangle` | Exclusive render-style toggle | — |
| X-ray render | `Scan` | Exclusive render-style toggle | — |
| Voxel render | `Boxes` | Exclusive render-style toggle; may compute voxels | — |
| BREP edges | `Spline` | Toggle; disabled without BREP edge data | — |
| Lens-colour opacity | `Layers` | Toggle plus hover/focus range flyout | — |
| Findings opacity | `Flag` | Toggle plus hover/focus range flyout | — |
| Section plane | `Slice` | Toggle and opens right rail | — |
| Perspective/orthographic | `Cuboid` | Two-state projection toggle | — |
| Fit part | `Maximize` | One-shot action | — |
| Reset viewport | `RotateCcw` | One-shot reset | — |
| Fit selected legend group | `Focus` | Contextual one-shot action | — |
| Ghost non-selection | `Ghost` | Contextual toggle | — |
| Isolate selection | `Eye` | Contextual toggle | — |
| Clear selection | Unicode `✕` | Contextual text/glyph action | — |
| Measure two points | `Ruler` | Interaction-mode toggle | — |

### Sidebar and global navigation

| Action/destination | Current icon | Notes | Design decision |
| --- | --- | --- | --- |
| Projects | `FolderKanban` | Disabled/coming soon | — |
| Parts | `Boxes` | Current destination | — |
| Review queue | `ClipboardCheck` | Disabled/coming soon | — |
| Stack presets | `Layers` | Disabled/coming soon | — |
| Materials | `Wrench` | Disabled/coming soon | — |
| Report row | `FileText` | Opens a published report | — |
| Add a part | `Plus` | Small icon button | — |
| Empty-state upload | `Upload` | Large dashed target | — |
| Part row | `Package` | Selects a part | — |
| Reprocess/cancel part job | `RefreshCw` | Spins while busy and changes action to Cancel | — |
| Advanced mode | `SlidersHorizontal` + Switch | Icon labels the row; Switch is the control | — |
| Theme | `Moon` / `Sun` | Icon and label both swap | — |
| Back from report | `ArrowLeft` | Text action | — |

### Pipeline, check, and execution actions

| Action/concept | Current icon | Notes | Design decision |
| --- | --- | --- | --- |
| Wall-thickness check | `Ruler` | Check card | — |
| Gap/clearance check | `Spline` | Check card | — |
| Ray-thickness check | `Ratio` | Advanced check card | — |
| Ray-gap check | `Radius` | Advanced check card | — |
| Sheet detection | `Layers` | Stats check card | — |
| Flat-pattern check | `Expand` | Stats check card | — |
| Bend-plan check | `ListOrdered` | Stats check card | — |
| Feature recognition | `Drill` | Exploration/NA check card | — |
| Reach study | `Eye` | Check card | — |
| CNC operation reach | `Axis3d` | Check card | — |
| Route aggregate reach | `ShieldCheck` | Check card | — |
| Laser operation | `Zap` | Operation header | — |
| Press-brake operation | `Hammer` | Operation header | — |
| CNC setup operation | `Compass` | Operation header | — |
| Generic operation/route | `Route` | Header or route-template CTA | — |
| Create plan / Add operation | `Plus` | Outline Catalyst CTA | — |
| Add CNC exploration | `Compass` | Outline Catalyst CTA | — |
| Publish report | `FileUp` | Solid Catalyst CTA | — |
| Remove check/operation | `X` | Hover-revealed icon button | — |
| Run | `Play` | Solid Catalyst CTA | — |
| Re-run / running | `RotateCw` | Spins while running | — |
| Pin explored threshold | `Pin` | Plain Catalyst CTA | — |
| Add/update field check | `BookmarkPlus` | Outline Catalyst CTA | — |
| Accept/reopen finding | no icon | Plain text Catalyst action | — |
| Impact dialog Cancel/Apply | no icon | Text Catalyst actions | — |

### Right-rail tools

| Tool/action | Current icon | Notes | Design decision |
| --- | --- | --- | --- |
| Configure / Advanced settings | `Settings2` | Disclosure heading | — |
| Open/close disclosure | `ChevronDown` | Rotates 180° | — |
| Direction picking | `Crosshair` | Mode button | — |
| Add direction/group | `Plus` | Catalyst CTA | — |
| Restore suppressed directions | `EyeOff` | Plain Catalyst CTA | — |
| Remove direction/group | `X` | Small icon button | — |
| Delete selected direction | `Trash2` | Destructive full-width action | — |
| Section rail | `Slice` | Rail identity and toolbar trigger | — |
| Snap section to picked face | `Crosshair` | Full-width local action | — |
| Section axis X/Y/Z/View | no icon | Segmented text control | — |
| Flip / Reset section | no icon | Compact text actions | — |
| Measure rail | `Ruler` | Rail identity and toolbar trigger | — |
| Close rail/tool | `X` | Low-emphasis icon action | — |
| Measure frame World/Face A/Face B | no icon | Segmented text control | — |
| Clear measure picks | no icon | Outlined text action | — |
| Edit PMI | `Pencil` | Compact text action | — |
| Export PMI | `Download` | Export action | — |
| Save PMI | `Save` | Compact text action | — |
| Add PMI entity | `Plus` | Compact text action | — |
| Pick PMI faces | `MousePointerClick`; active `Check` | State-changing text action | — |
| Delete PMI entity | `Trash2` | Icon-only destructive action | — |
| PMI scopes/modifiers | GD&T glyphs or text | Domain symbols, not Lucide | — |

## 6. Known seams to resolve during the design pass

1. **Toolbar primitives are duplicated.** `AnalysisToolbar.tsx` and
   `ViewportToolbar.tsx` repeat the same button-state class strings instead of
   sharing a component/token.
2. **Raw 32 px toolbar buttons do not get Catalyst's 44 px coarse-pointer touch
   target or its explicit blue focus ring.** Their visual state is coherent, but
   keyboard/touch treatment is not shared with action buttons.
3. **Tooltips are native `title` attributes.** There is no V2 tooltip component,
   so timing, layout, multiline help, and touch behaviour are browser-defined.
4. **`data-slot="icon"` is not applied consistently.** Catalyst only guarantees
   its 16 px sizing and semantic icon tint when the attribute is present; a few
   Run/Re-run buttons pass a raw Lucide component without it.
5. **One icon can mean several unrelated things.** `Layers` is the clearest case:
   navigation presets, opacity, mold assignment, CNC classification, sheet roles,
   and tube roles.
6. **Action and result metaphors overlap.** `Eye`, `Compass`, `Ruler`, `Spline`,
   and `Layers` appear both as tools/actions and as data-lens identities. Context
   currently carries much of the meaning.
7. **The V1 control island is visibly separate.** Its filled blue-grey and green
   buttons should not be used as a reference for new V2 icons or controls.
8. **Some clear/remove actions use Unicode while others use Lucide `X` or
   `Trash2`.** The review should choose an explicit distinction between close,
   remove-from-list, clear-state, and destructive delete.

## 7. Review criteria for each replacement

For every row above, decide:

1. Does the icon identify the **result being viewed**, rather than merely the
   action that produced it?
2. Is it distinguishable from adjacent toolbar/menu icons at 16 px without a
   label?
3. Does the same metaphor mean the same thing elsewhere in the UI?
4. Does active inversion preserve the shape, or does it collapse into a white
   blob?
5. Is a standard Lucide icon sufficient? If not, can a custom 24 × 24, 2 px
   outline icon express the CAD/DFM concept without relying on colour?
6. Should the item remain icon-only, move into the labelled Wrench menu, or become
   a text/action button in a rail?

## 8. Source of truth

- Lens meanings and icons: `frontend/src/v2/lenses.ts`
- Check meanings and icons: `frontend/src/v2/analyses.ts`,
  `frontend/src/v2/checks/catalog.ts`
- Floating inspection toolbar: `frontend/src/v2/workspace/AnalysisToolbar.tsx`
- Floating viewport toolbar: `frontend/src/v2/workspace/ViewportToolbar.tsx`
- Catalyst action button: `frontend/src/catalyst/button.tsx`
- Sidebar rows/actions: `frontend/src/catalyst/sidebar.tsx`,
  `frontend/src/v2/nav/AppSidebar.tsx`
- Pipeline cards/actions: `frontend/src/v2/workspace/PipelineRail.tsx`
- Right-rail local actions: `frontend/src/v2/workspace/*Rail.tsx`
- Legacy embedded controls: `frontend/src/v2/workspace/v1-controls.css`
- Global V2 theme: `frontend/src/v2/app.css`
