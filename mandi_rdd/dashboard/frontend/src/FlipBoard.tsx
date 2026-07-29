import React, { useRef, useEffect, useMemo } from "react";
import { Streamlit } from "streamlit-component-lib";

/* ═══════════════════════════════════════════════════════════
   MandiIQ Flip-Board KPI Hero — MandiIQ Design.

   Pure black canvas · Lime (#d7ff00) single accent
   Glass cards · Crosshair corner markers on hover
   Flip-digit animation on value change
   ═══════════════════════════════════════════════════════════ */

export interface KpiItem {
  label: string;
  value: string;
  raw: number | null;
  prefix?: string;
  suffix?: string;
}

export interface KpiData {
  effect: KpiItem;
  avg_price: KpiItem;
  districts: KpiItem;
  mape: KpiItem;
}

// MandiIQ-inspired palette
const COLORS = {
  bg: "#000000",
  surface: "#0a0a0a",
  glass: "rgba(255,255,255,0.03)",
  glassHover: "rgba(255,255,255,0.06)",
  primary: "#d7ff00",
  paper: "#ffffff",
  textHigh: "#bababa",
  textMed: "#7e7e7e",
  textLow: "#555555",
  rust: "#D9663B",
  sage: "#8FAE89",
  hairline: "rgba(255,255,255,0.07)",
  hairlineStrong: "rgba(255,255,255,0.14)",
};

const STAGGER_MS = 40;
const FLIP_DURATION_MS = 300;

function formatDigitString(value: string): string[] {
  return value.split("");
}

// ── Single digit cell with flip animation ──

interface DigitCellProps {
  char: string;
  isFlipping: boolean;
  staggerDelay: number;
}

const DigitCell = React.memo(function DigitCell({
  char,
  isFlipping,
  staggerDelay,
}: DigitCellProps) {
  const prefersReduced = useMemo(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }, []);

  const style: React.CSSProperties = prefersReduced
    ? {}
    : isFlipping
    ? {
        animation: `flipDigit ${FLIP_DURATION_MS}ms ease-out ${staggerDelay}ms both`,
      }
    : {};

  return (
    <span className="flip-digit" style={style}>
      {char}
    </span>
  );
});

// ── Single KPI card styling ──

interface KpiCardProps {
  item: KpiItem;
  prevValue: number | null;
}

function KpiCard({ item, prevValue }: KpiCardProps) {
  const hasChanged = item.raw !== prevValue;
  const chars = formatDigitString(item.value);
  return (
    <div className="flip-kpi-card">
      <div className="flip-kpi-label">{item.label}</div>
      <div className="flip-kpi-value">
        {item.prefix && (
          <span className="flip-kpi-prefix">{item.prefix}</span>
        )}
        <span className="flip-kpi-digits">
          {chars.map((ch, i) => (
            <DigitCell
              key={i}
              char={ch}
              isFlipping={hasChanged}
              staggerDelay={i * STAGGER_MS}
            />
          ))}
        </span>
        {item.suffix && (
          <span className="flip-kpi-suffix">{item.suffix}</span>
        )}
      </div>
    </div>
  );
}

// ── Main FlipBoard component ──

interface FlipBoardProps {
  kpis: KpiData;
}

export default function FlipBoard({ kpis }: FlipBoardProps) {
  const prevRef = useRef<{
    effect: number | null;
    avg_price: number | null;
    districts: number | null;
    mape: number | null;
  }>({
    effect: NaN,
    avg_price: NaN,
    districts: NaN,
    mape: NaN,
  });

  const prev = prevRef.current;

  useEffect(() => {
    const timer = setTimeout(() => {
      prevRef.current = {
        effect: kpis.effect.raw,
        avg_price: kpis.avg_price.raw,
        districts: kpis.districts.raw,
        mape: kpis.mape.raw,
      };
    }, STAGGER_MS * 20 + FLIP_DURATION_MS + 100);
    return () => clearTimeout(timer);
  }, [kpis.effect.raw, kpis.avg_price.raw, kpis.districts.raw, kpis.mape.raw]);

  useEffect(() => {
    Streamlit.setFrameHeight();
  });

  const kpiList = [kpis.effect, kpis.avg_price, kpis.districts, kpis.mape];

  return (
    <div className="flip-board-root">
      <style>{`
        .flip-board-root {
          display: flex;
          flex-wrap: wrap;
          gap: 1rem;
          padding: 0.75rem 0;
          font-family: 'IBM Plex Mono', 'JetBrains Mono', 'Fira Code', monospace;
          background: ${COLORS.bg};
        }

        .flip-kpi-card {
          flex: 1 1 200px;
          background: linear-gradient(135deg, ${COLORS.glass} 0%, rgba(255,255,255,0.005) 100%);
          border: 1px solid ${COLORS.hairline};
          border-radius: 8px;
          padding: 1.2rem 1.2rem;
          min-width: 160px;
          position: relative;
          overflow: hidden;
          transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1),
                      border-color 0.35s ease;
        }

        /* Crosshair corner markers — active-target pattern */
        .flip-kpi-card::before {
          content: '';
          position: absolute;
          top: -1px;
          left: -1px;
          width: 10px;
          height: 10px;
          border-top: 1.5px solid ${COLORS.primary};
          border-left: 1.5px solid ${COLORS.primary};
          opacity: 0;
          transition: opacity 0.35s ease;
          pointer-events: none;
        }

        .flip-kpi-card::after {
          content: '';
          position: absolute;
          bottom: -1px;
          right: -1px;
          width: 10px;
          height: 10px;
          border-bottom: 1.5px solid ${COLORS.primary};
          border-right: 1.5px solid ${COLORS.primary};
          opacity: 0;
          transition: opacity 0.35s ease;
          pointer-events: none;
        }

        .flip-kpi-card:hover::before,
        .flip-kpi-card:hover::after {
          opacity: 1;
        }

        .flip-kpi-card:hover {
          border-color: rgba(215, 255, 0, 0.15);
          transform: translateY(-2px);
        }

        .flip-kpi-label {
          font-family: 'IBM Plex Sans', 'Inter', system-ui, sans-serif;
          font-size: 0.68rem;
          font-weight: 500;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: ${COLORS.textMed};
          margin-bottom: 0.5rem;
        }

        .flip-kpi-value {
          display: flex;
          align-items: baseline;
          gap: 4px;
        }

        .flip-kpi-prefix,
        .flip-kpi-suffix {
          font-size: 0.95rem;
          color: ${COLORS.textMed};
          font-weight: 500;
        }

        .flip-kpi-digits {
          display: inline-flex;
          font-size: 1.7rem;
          font-weight: 600;
          color: ${COLORS.paper};
          line-height: 1;
        }

        .flip-digit {
          display: inline-block;
          position: relative;
          min-width: 0.6em;
          text-align: center;
        }

        @keyframes flipDigit {
          0% {
            transform: rotateX(0deg);
            opacity: 1;
          }
          50% {
            transform: rotateX(-90deg);
            opacity: 0.3;
          }
          100% {
            transform: rotateX(0deg);
            opacity: 1;
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .flip-digit {
            animation: none !important;
          }
        }

        @media (max-width: 768px) {
          .flip-board-root {
            gap: 0.7rem;
          }
          .flip-kpi-digits {
            font-size: 1.3rem;
          }
          .flip-kpi-card {
            min-width: 130px;
            padding: 0.9rem 1rem;
          }
        }
      `}</style>

      {kpiList.map((item, i) => (
        <KpiCard
          key={item.label}
          item={item}
          prevValue={prev[item.label === "RDD Effect" ? "effect"
                     : item.label === "Avg Price" ? "avg_price"
                     : item.label === "Districts" ? "districts"
                     : "mape"]}
        />
      ))}
    </div>
  );
}

