/** Small animated diagrams for each camera move — the rig panel's visual picker. */

interface Glyph {
  anim: string;
  paths?: string[];
  rings?: [number, number, number][];
}

const G: Record<string, Glyph> = {
  static: { anim: 'pulse', paths: ['M24 10h16v24H24z'], rings: [[32, 22, 2.4]] },
  dolly_in: { anim: 'punch', paths: ['M40 9h16v26H40z', 'M4 22h28'] },
  dolly_out: { anim: 'punch-out', paths: ['M8 9h16v26H8z', 'M60 22H34'] },
  truck_left: { anim: 'slide-left', paths: ['M54 22H14'] },
  truck_right: { anim: 'slide-right', paths: ['M10 22h40'] },
  pan_left: { anim: 'sway-left', paths: ['M54 13C40 27 28 28 15 21'] },
  pan_right: { anim: 'sway-right', paths: ['M10 13C24 27 36 28 49 21'] },
  tilt_up: { anim: 'rise', paths: ['M32 38V12'] },
  tilt_down: { anim: 'fall', paths: ['M32 6V32'] },
  crane_up: { anim: 'rise', paths: ['M12 36L46 12'] },
  crane_down: { anim: 'fall', paths: ['M18 10L50 33'] },
  orbit_left: { anim: 'spin-left', paths: ['M48 22A16 16 0 1 1 42 7'] },
  orbit_right: { anim: 'spin-right', paths: ['M16 22A16 16 0 1 0 22 7'] },
  handheld: { anim: 'wobble', paths: ['M4 24l7-7 6 8 6-10 7 11 7-8 7 10 6-7'] },
  push_in_fast: { anim: 'punch-fast', paths: ['M42 9h14v26H42z', 'M4 22h26', 'M16 13l9 9-9 9'] },
  whip_pan: { anim: 'sway-fast', paths: ['M56 16C42 26 26 28 10 20', 'M14 32h16', 'M20 38h12'] },
  arc: { anim: 'sway-left', paths: ['M18 37A19 19 0 0 1 46 12'] },
  rack_focus: { anim: 'focus', rings: [[18, 22, 7], [33, 22, 4.5], [46, 22, 2.6]] },
  zoom_in: { anim: 'punch', paths: ['M10 9h44v26H10z', 'M21 15h22v14H21z', 'M32 41v-7'] },
  zoom_out: { anim: 'punch-out', paths: ['M10 9h44v26H10z', 'M24 17h16v10H24z', 'M32 32v7'] },
};

const FALLBACK: Glyph = G.static;

export function MoveGlyph({ move, size = 40 }: { move: string; size?: number }) {
  const glyph = G[move] ?? FALLBACK;
  return (
    <svg
      className={`glyph glyph-${glyph.anim}`}
      width={size}
      height={Math.round(size * 0.69)}
      viewBox="0 0 64 44"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <marker
          id="cm-arrow"
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="4.5"
          markerHeight="4.5"
          orient="auto-start-reverse"
        >
          <path d="M0 0 L10 5 L0 10 z" fill="currentColor" stroke="none" />
        </marker>
      </defs>
      <g fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
        {(glyph.paths ?? []).map((d, i) => (
          <path key={i} d={d} markerEnd="url(#cm-arrow)" />
        ))}
        {(glyph.rings ?? []).map(([cx, cy, r], i) => (
          <circle key={`r${i}`} cx={cx} cy={cy} r={r} />
        ))}
      </g>
    </svg>
  );
}

export const MOVE_GLYPH_NAMES = Object.keys(G);
