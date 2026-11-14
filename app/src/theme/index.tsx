import React, { createContext, useContext, useMemo, useState } from "react";
import { useColorScheme } from "react-native";

/** Tokens read from the Figma file (spanner.* variables). Light danger/seal values are not in Figma: assumed. */
export interface Palette {
  base: string;
  raised: string;
  textPrimary: string;
  textSecondary: string;
  borderSubtle: string;
  borderStrong: string;
  action: string;
  onAction: string;
  dangerFg: string;
  dangerSurface: string;
  onDanger: string;
  seal: string;
}

const dark: Palette = {
  base: "#101512",
  raised: "#253027",
  textPrimary: "#f2f2e9",
  textSecondary: "#a0a99f",
  borderSubtle: "#354137",
  borderStrong: "#354137",
  action: "#caf879",
  onAction: "#172415",
  dangerFg: "#f3917c",
  dangerSurface: "#2c211b",
  onDanger: "#172415",
  seal: "#caf879",
};

const light: Palette = {
  base: "#f3f2ec",
  raised: "#e4e8dc",
  textPrimary: "#16241b",
  textSecondary: "#626d61",
  borderSubtle: "#d1d6c9",
  borderStrong: "#cbd3c4",
  action: "#214e31",
  onAction: "#f6f7ef",
  dangerFg: "#b7332d",
  dangerSurface: "#f6e4dd",
  onDanger: "#ffffff",
  seal: "#214e31",
};

/** Camera overlay tokens are the same in both themes. */
export const camera = {
  surface: "#101512",
  accent: "#caf879",
  text: "#ffffff",
  tint: "rgba(12,23,17,0.13)",
  scrimStrong: "rgba(0,0,0,0.85)",
  scrimClear: "rgba(0,0,0,0)",
  scrimBottom: "rgba(0,0,0,0.92)",
};

export const hud = { info: "#92e6ef", ok: "#caf879", warn: "#ffd166", danger: "#f3917c" };
export const callFill = "#b7332d";

export const radius = { r2: 2, r6: 6, r8: 8, r12: 12, r36: 36, pill: 999 };

export const font = {
  regular: "Inter_400Regular",
  medium: "Inter_500Medium",
  semibold: "Inter_600SemiBold",
  mono: "JetBrainsMono_400Regular",
};

type Mode = "dark" | "light";
interface ThemeCtx {
  mode: Mode;
  c: Palette;
  toggle: () => void;
}
const Ctx = createContext<ThemeCtx>({ mode: "dark", c: dark, toggle: () => {} });

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const system = useColorScheme();
  const [override, setOverride] = useState<Mode | null>(null);
  const mode: Mode = override ?? (system === "light" ? "light" : "dark");
  const value = useMemo<ThemeCtx>(
    () => ({ mode, c: mode === "dark" ? dark : light, toggle: () => setOverride(mode === "dark" ? "light" : "dark") }),
    [mode],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
