import React from "react";
import { View } from "react-native";
import { Image } from "expo-image";
import { useTheme } from "../theme";
import { diagramAssets } from "./diagramAssets";

/**
 * The "chain onto the front gear" 2D guide from the Figma file (Diagram_2D_Large / Diagram_2D_Compact).
 * Artwork is laid out in the design's own 354 x 309.75 box and scaled to `width`, so the same component
 * serves the large step board and the compact side-by-side panel.
 */
const W = 354;
const H = 309.75;

type Inset = readonly [top: number, right: number, bottom: number, left: number]; // percent of the artwork box
type Vec = readonly [cqw: number, cqh: number];

interface RotatedLink {
  inset: Inset;
  /** Link thickness and length as vectors in container-relative units (cqw, cqh), as exported from Figma. */
  h: Vec;
  w: Vec;
  rot: number;
}

// Chain wrapped round the front gear: 18 links, 175deg round to 5deg in 10deg steps.
const WRAPPED: RotatedLink[] = [
  { inset: [25.17, 56.71, 71.89, 36.8], h: [-2.72895, -78.5651], w: [-97.2711, 21.4349], rot: 175 },
  { inset: [25.44, 60.86, 70.45, 32.5], h: [-7.91248, -54.4784], w: [-92.0875, 45.5216], rot: 165 },
  { inset: [26.59, 64.91, 68.25, 28.49], h: [-13.008, -40.7471], w: [-86.992, 59.2529], rot: 155 },
  { inset: [28.59, 68.74, 65.35, 24.9], h: [-18.3364, -31.4113], w: [-81.6636, 68.5887], rot: 145 },
  { inset: [31.39, 72.24, 61.85, 21.84], h: [-24.2809, -24.2809], w: [-75.7191, 75.7191], rot: 135 },
  { inset: [34.89, 75.31, 57.85, 19.39], h: [-31.4113, -18.3364], w: [-68.5887, 81.6636], rot: 125 },
  { inset: [38.99, 77.84, 53.46, 17.64], h: [-40.7471, -13.008], w: [-59.2529, 86.992], rot: 115 },
  { inset: [43.57, 79.77, 48.84, 16.63], h: [-54.4784, -7.91248], w: [-45.5216, 92.0875], rot: 105 },
  { inset: [48.49, 81.02, 44.1, 16.4], h: [-78.5651, -2.72895], w: [-21.4349, 97.2711], rot: 95 },
  { inset: [53.39, 81.02, 39.2, 16.4], h: [-78.5651, 2.72895], w: [21.4349, 97.2711], rot: 85 },
  { inset: [58.12, 79.77, 34.29, 16.63], h: [-54.4784, 7.91248], w: [45.5216, 92.0875], rot: 75 },
  { inset: [62.75, 77.84, 29.71, 17.64], h: [-40.7471, 13.008], w: [59.2529, 86.992], rot: 65 },
  { inset: [67.13, 75.31, 25.6, 19.39], h: [-31.4113, 18.3364], w: [68.5887, 81.6636], rot: 55 },
  { inset: [71.14, 72.25, 22.1, 21.84], h: [-24.2809, 24.2809], w: [75.7191, 75.7191], rot: 45 },
  { inset: [74.64, 68.74, 19.31, 24.9], h: [-18.3364, 31.4113], w: [81.6636, 68.5887], rot: 35 },
  { inset: [77.53, 64.91, 17.3, 28.49], h: [-13.008, 40.7471], w: [86.992, 59.2529], rot: 25 },
  { inset: [79.73, 60.86, 16.15, 32.5], h: [-7.91248, 54.4784], w: [92.0875, 45.5216], rot: 15 },
  { inset: [81.17, 56.71, 15.89, 36.8], h: [-2.72895, 78.5651], w: [97.2711, 21.4349], rot: 5 },
];

// Loose tail of the chain hanging off the bottom of the gear: 8 links.
const TAIL: RotatedLink[] = [
  { inset: [81.62, 52.44, 15.56, 41.1], h: [-2.19317, 82.0975], w: [97.8068, 17.9025], rot: 4 },
  { inset: [82, 48.17, 15.43, 45.43], h: [-1.10741, 90.1795], w: [98.8926, 9.82047], rot: 2 },
  { inset: [82, 43.87, 15.43, 49.73], h: [1.10741, 90.1795], w: [98.8926, -9.82047], rot: -2 },
  { inset: [81.41, 39.52, 15.52, 53.96], h: [3.2605, 75.3146], w: [96.7395, -24.6854], rot: -6 },
  { inset: [80.49, 35.23, 15.97, 58.18], h: [5.35169, 64.5216], w: [94.6483, -35.4784], rot: -10 },
  { inset: [79.24, 31, 16.76, 62.37], h: [7.40331, 56.2582], w: [92.5967, -43.7418], rot: -14 },
  { inset: [77.66, 26.86, 17.89, 66.49], h: [9.43607, 49.6709], w: [90.5639, -50.3291], rot: -18 },
  { inset: [75.95, 22.79, 19.39, 70.56], h: [10.4516, 46.8378], w: [89.5484, -53.1622], rot: -20 },
];

// The four links lifted above the gear, ready to be placed (no rotation).
const PLACEMENT: Inset[] = [
  [20.98, 61.1, 76.7, 32.57],
  [20.98, 56.8, 76.7, 36.87],
  [20.98, 52.49, 76.7, 41.17],
  [20.98, 48.19, 76.7, 45.48],
];

const CHAINRING: Inset = [26.74, 33.39, 17.45, 17.77];
const CRANK: Inset = [51.05, 39.55, 19.9, 38.92];
const ARROW: Inset = [11.79, 54.84, 80.54, 39.22];

// How far each exported SVG bleeds outside its layer box (stroke), in percent of the layer: [top, right, bottom, left].
const BLEED_LINK: Inset = [-9.23, -2.96, -9.23, -2.96];
const BLEED_RING: Inset = [-0.31, -0.31, -0.31, -0.31];
const BLEED_CRANK: Inset = [-1.23, -1.46, -1.23, -1.45];
const BLEED_ARROW: Inset = [-2.91, -3.29, -2.91, -3.29];

const box = ([t, r, b, l]: Inset, pw: number, ph: number) => ({
  left: (l / 100) * pw,
  top: (t / 100) * ph,
  width: ((100 - l - r) / 100) * pw,
  height: ((100 - t - b) / 100) * ph,
});

/** A layer's artwork, drawn slightly larger than the layer by the bleed. */
function Art({ src, w, h, bleed }: { src: number; w: number; h: number; bleed: Inset }) {
  const [t, r, b, l] = bleed;
  return (
    <Image
      source={src}
      contentFit="fill"
      style={{
        position: "absolute",
        left: (l / 100) * w,
        top: (t / 100) * h,
        width: w * (1 - (l + r) / 100),
        height: h * (1 - (t + b) / 100),
      }}
    />
  );
}

export function Diagram2D({ width }: { width: number }) {
  const { mode } = useTheme();
  const a = diagramAssets[mode];
  const k = width / W;
  const pw = W * k;
  const ph = H * k;

  const layer = (inset: Inset, src: number, bleed: Inset, key: string) => {
    const b = box(inset, pw, ph);
    return (
      <View key={key} style={{ position: "absolute", ...b }}>
        <Art src={src} w={b.width} h={b.height} bleed={bleed} />
      </View>
    );
  };

  const rotated = (l: RotatedLink, src: number, key: string) => {
    const b = box(l.inset, pw, ph);
    const lw = Math.hypot((l.w[0] * b.width) / 100, (l.w[1] * b.height) / 100);
    const lh = Math.hypot((l.h[0] * b.width) / 100, (l.h[1] * b.height) / 100);
    return (
      <View key={key} style={{ position: "absolute", ...b }}>
        <View
          style={{
            position: "absolute",
            left: (b.width - lw) / 2,
            top: (b.height - lh) / 2,
            width: lw,
            height: lh,
            transform: [{ rotate: `${l.rot}deg` }],
          }}
        >
          <Art src={src} w={lw} h={lh} bleed={BLEED_LINK} />
        </View>
      </View>
    );
  };

  return (
    <View style={{ width: pw, height: ph, overflow: "hidden" }} accessibilityLabel="2D guide: lift the chain and rest it on the top teeth">
      {layer(CHAINRING, a.chainring, BLEED_RING, "ring")}
      {layer(CRANK, a.crank, BLEED_CRANK, "crank")}
      {WRAPPED.map((l, i) => rotated(l, a.links[i], `w${i}`))}
      {TAIL.map((l, i) => rotated(l, a.links[WRAPPED.length + i], `t${i}`))}
      {PLACEMENT.map((inset, i) => layer(inset, a.links[WRAPPED.length + TAIL.length + i], BLEED_LINK, `p${i}`))}
      {layer(ARROW, a.arrow, BLEED_ARROW, "arrow")}
    </View>
  );
}
