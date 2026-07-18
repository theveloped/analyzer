/** Static CAD orientation triad, bottom-right of the viewer (page 4a). */
export function OrientationTriad() {
  return (
    <div className="absolute bottom-3 right-3 size-[68px] rounded-lg border bg-background/92 shadow-sm backdrop-blur">
      <svg viewBox="0 0 74 74" className="size-full">
        <g strokeWidth="2" fill="none">
          <path d="M37,44 L37,14" stroke="#2a78d6" />
          <path d="M37,44 L62,58" stroke="#d64a4a" />
          <path d="M37,44 L14,60" stroke="#4a9d6a" />
        </g>
        <circle cx="37" cy="44" r="3" fill="currentColor" />
        <text x="37" y="11" fontSize="8" textAnchor="middle" fill="#2a78d6" fontFamily="monospace">Z</text>
        <text x="66" y="62" fontSize="8" fill="#d64a4a" fontFamily="monospace">X</text>
        <text x="6" y="66" fontSize="8" fill="#4a9d6a" fontFamily="monospace">Y</text>
      </svg>
    </div>
  );
}
