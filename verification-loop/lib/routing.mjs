const has = (t, words) => words.some(w => t.includes(w));
const DOM = ['shows $', 'displays value', 'shows —', '— cell', 'table row', 'count of',
  'form shows', 'input contains', 'checkbox', 'dropdown', 'navigates to', 'url changes',
  'toast', 'banner', 'not ingested', 'empty panel', 'api returns', 'innertext'];
const VISION = ['chart renders', 'chart appears', 'bars are visible', 'visible bars', 'canvas',
  'color is', 'transparent', 'overlay', 'z-index', 'layout', 'clipped', 'responsive', 'skeleton'];
const INTERACT = ['click', 'hover', 'select', 'type ', 'submit'];

export function selectObservationLayer(criterion) {
  const t = String(criterion || '').toLowerCase();
  if (has(t, VISION)) return 'VISION';        // vision first: render defects override
  if (has(t, INTERACT)) return 'DOM_INTERACTION';
  if (has(t, DOM)) return 'DOM';
  return 'DOM';
}
