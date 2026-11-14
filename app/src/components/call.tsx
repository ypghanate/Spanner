import React, { forwardRef, useEffect, useRef, useState } from "react";
import { Animated, Easing, Pressable, StyleSheet, View, ViewStyle, useWindowDimensions } from "react-native";
import { Image } from "expo-image";
import { CameraView } from "expo-camera";
import { LinearGradient } from "expo-linear-gradient";
import type { Detection, HudCommand } from "../../types/contracts";
import { callFill, camera, hud as hudColors, radius, useTheme } from "../theme";
import { useSpanner } from "../state/SpannerProvider";
import { CallEndButton, Icon, icons, images, Txt, Waveform } from "./ui";

/** Live rear camera with the design's tint and top / bottom scrims. */
export const CameraBackdrop = forwardRef<
  CameraView,
  { children?: React.ReactNode; permitted: boolean; mode?: "full" | "hidden" }
>(function CameraBackdrop({ children, permitted, mode = "full" }, ref) {
  // In the 3D / 2D views the camera stays mounted (hidden) so frames keep flowing.
  if (mode === "hidden") {
    return (
      <View pointerEvents="none" style={{ position: "absolute", right: 0, top: 0, width: 64, height: 64, opacity: 0.01 }}>
        {permitted ? <CameraView ref={ref} style={StyleSheet.absoluteFill} facing="back" animateShutter={false} mute /> : null}
      </View>
    );
  }
  return (
    <View style={[StyleSheet.absoluteFill, { backgroundColor: camera.surface }]}>
      {permitted ? (
        <CameraView ref={ref} style={StyleSheet.absoluteFill} facing="back" animateShutter={false} mute />
      ) : (
        <Image source={images.liveCamera} style={StyleSheet.absoluteFill} contentFit="cover" />
      )}
      <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: camera.tint }]} />
      <LinearGradient pointerEvents="none" colors={[camera.scrimStrong, camera.scrimClear]} style={styles.topScrim} />
      <LinearGradient pointerEvents="none" colors={[camera.scrimClear, camera.scrimBottom]} style={styles.bottomScrim} />
      {children}
    </View>
  );
});

/**
 * The live camera in a rounded box, for the side-by-side guide ("split") and the demo's picture-in-picture ("pip").
 * Frames keep flowing from here while the diagram view is up.
 */
export const CameraBox = forwardRef<CameraView, { permitted: boolean; variant: "split" | "pip"; style?: ViewStyle }>(function CameraBox(
  { permitted, variant, style },
  ref,
) {
  const split = variant === "split";
  return (
    <View pointerEvents="none" style={[{ borderRadius: radius.r12, overflow: "hidden", backgroundColor: camera.surface }, style]}>
      {permitted ? (
        <CameraView ref={ref} style={StyleSheet.absoluteFill} facing="back" animateShutter={false} mute />
      ) : (
        <Image source={split ? images.liveCamera : images.pip} style={StyleSheet.absoluteFill} contentFit="cover" />
      )}
      {split ? (
        <View style={styles.splitLabel}>
          <Txt weight="medium" size={10} color={camera.text}>Your camera</Txt>
        </View>
      ) : (
        <View style={styles.pipLabel}>
          <Txt weight="medium" size={9} color={camera.text}>Your camera</Txt>
        </View>
      )}
    </View>
  );
});

export function useElapsed(startedAt: number | null) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  return `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
}

export function CallHeader() {
  const { startedAt, connected } = useSpanner();
  const elapsed = useElapsed(startedAt);
  return (
    <View style={styles.header}>
      <Image source={images.logo} style={{ width: 126, height: 22, marginLeft: 10 }} contentFit="contain" />
      <View style={styles.durationPill}>
        <View style={[styles.liveDot, { backgroundColor: connected ? camera.accent : hudColors.warn }]} />
        <Txt weight="medium" size={12} color={camera.text}>{elapsed}</Txt>
      </View>
    </View>
  );
}

export function DurationPillStandalone() {
  const { startedAt } = useSpanner();
  const elapsed = useElapsed(startedAt);
  return (
    <View style={styles.durationPill}>
      <View style={[styles.liveDot, { backgroundColor: camera.accent }]} />
      <Txt weight="medium" size={12} color={camera.text}>{elapsed}</Txt>
    </View>
  );
}

/** Small dark bar on the camera, e.g. "01 / Diagnose · show me what's wrong". */
export function PhaseBar({ text }: { text: string }) {
  return (
    <View style={styles.phaseBar}>
      <Txt weight="medium" size={12} color={camera.text}>{text}</Txt>
    </View>
  );
}

export function UserCard({ text }: { text: string }) {
  return (
    <View style={styles.userCard}>
      <Txt weight="medium" size={10} color={camera.accent}>YOU · EXPLAINING THE PROBLEM</Txt>
      <Txt weight="medium" size={17} color={camera.text} style={{ marginTop: 14 }}>{`“${text}”`}</Txt>
    </View>
  );
}

/** "WHAT I'M SEEING": what diagnosing concluded, shown once the problem is understood. */
export function FindingCard({ title, detail }: { title: string | null; detail: string }) {
  return (
    <View style={styles.finding}>
      <Txt weight="medium" size={10} color={camera.accent}>WHAT I’M SEEING</Txt>
      {title ? <Txt weight="medium" size={24} color={camera.text} numberOfLines={2} style={{ marginTop: 9 }}>{title}</Txt> : null}
      <Txt size={12} color={camera.text} numberOfLines={3} style={{ marginTop: title ? 8 : 9 }}>{detail}</Txt>
    </View>
  );
}

/** Collapsed: "2 / 5  Refit the chain  +" with a progress bar. Tap to expand the instruction and common mistakes. */
export function StepProgressCard() {
  const { c } = useTheme();
  const { plan, currentStep } = useSpanner();
  const [open, setOpen] = useState(false);
  if (!plan || currentStep == null) return null;
  const step = plan.steps.find((x) => x.index === currentStep);
  const total = plan.steps.length;
  const title = step ? firstSentence(step.instruction) : "";
  return (
    <Pressable onPress={() => setOpen((o) => !o)} accessibilityRole="button" accessibilityLabel="Toggle step details" style={[styles.stepCard, { backgroundColor: c.raised }]}>
      <View style={styles.stepHeader}>
        <View style={{ flexDirection: "row", gap: 4 }}>
          <Txt weight="semibold" size={13} color={c.action === "#214e31" ? camera.accent : c.action}>{currentStep}</Txt>
          <Txt size={13} color={c.textSecondary}>/</Txt>
          <Txt size={13} color={c.textSecondary}>{total}</Txt>
        </View>
        <Txt weight="semibold" size={15} numberOfLines={open ? 3 : 1} style={{ flex: 1 }}>{title}</Txt>
        <Txt size={20} color={c.textSecondary}>{open ? "−" : "+"}</Txt>
      </View>
      <View style={[styles.track, { backgroundColor: c.borderSubtle }]}>
        <View style={[styles.fill, { width: `${(currentStep / total) * 100}%`, backgroundColor: camera.accent }]} />
      </View>
      {open && step ? (
        <View style={{ gap: 8 }}>
          {step.instruction !== title ? <Txt size={13} color={c.textPrimary}>{step.instruction}</Txt> : null}
          {step.parts.length ? <Txt size={11} color={c.textSecondary}>Parts: {step.parts.join(" · ")}</Txt> : null}
          {step.common_mistakes.length ? <Txt size={11} color={hudColors.warn}>Watch out: {step.common_mistakes.join("; ")}</Txt> : null}
        </View>
      ) : null}
    </Pressable>
  );
}

const firstSentence = (t: string) => {
  const m = t.match(/^[^.!?]+/);
  return (m ? m[0] : t).trim();
};

const COLORS = hudColors;

interface Box { x: number; y: number; w: number; h: number; label: string; color: string; pulse?: boolean }

function toBoxes(hud: HudCommand[], detections: Detection[], warn: boolean): Box[] {
  const out: Box[] = [];
  for (const h of hud) {
    if (h.box && (h.op === "label" || h.op === "circle" || h.op === "arrow")) {
      out.push({ ...h.box, label: h.text ?? h.target_label ?? "", color: COLORS[h.color], pulse: warn && h.color === "warn" });
    }
  }
  if (!out.length) {
    for (const d of detections.filter((d) => d.score >= 0.5).slice(0, 3)) {
      out.push({ ...d.box, label: d.label, color: COLORS.info });
    }
  }
  return out;
}

/** Bounding boxes from HUD commands (or raw detections) drawn over the camera. Boxes are normalised 0-1 from the top-left. */
export function HudBoxes({ warn = false }: { warn?: boolean }) {
  const { hud, detections } = useSpanner();
  const { width, height } = useWindowDimensions();
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!warn) return;
    const l = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 650, easing: Easing.out(Easing.quad), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 0, useNativeDriver: true }),
      ]),
    );
    l.start();
    return () => l.stop();
  }, [warn, pulse]);
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      {toBoxes(hud, detections, warn).map((b, i) => {
        const frame = { left: b.x * width, top: b.y * height, width: b.w * width, height: b.h * height };
        return (
          <View key={i} style={[styles.hudBox, frame, { borderColor: b.color }]}>
            {b.label ? (
              <View style={styles.hudPill}>
                <Txt weight="medium" size={11} color={camera.text} numberOfLines={2}>{b.label}</Txt>
              </View>
            ) : null}
            {b.pulse ? (
              <Animated.View
                style={[
                  StyleSheet.absoluteFill,
                  { borderWidth: 2, borderColor: b.color, borderRadius: radius.r12, opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.9, 0] }), transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.12] }) }] },
                ]}
              />
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

export function SafetyBanner({ message, severity }: { message: string; severity: "warn" | "danger" }) {
  const { c } = useTheme();
  const danger = severity === "danger";
  return (
    <View style={[styles.banner, { backgroundColor: danger ? c.dangerSurface : camera.surface }]}>
      <Icon src={danger ? icons.safetyDanger : icons.safetyWarn} size={20} />
      <Txt weight="medium" size={12} color={danger ? c.dangerFg : hudColors.warn} style={{ flex: 1 }}>{message}</Txt>
    </View>
  );
}

/** Dark caption card with the voice status pill. Tap the status while Spanner speaks to interrupt. */
export function SpokenCaption({ text, speaking, listening }: { text: string; speaking: boolean; listening?: boolean }) {
  const { interrupt } = useSpanner();
  return (
    <View style={styles.caption}>
      <Pressable disabled={!speaking} onPress={interrupt} accessibilityLabel="Stop speaking" style={styles.voiceStatus}>
        <View style={{ width: 28 }}><Waveform active={speaking || !!listening} /></View>
        <Txt weight="medium" size={11} color={camera.accent}>{speaking ? "Spanner is speaking" : "Listening to you"}</Txt>
      </Pressable>
      <Txt weight="medium" size={18} color={camera.text}>{text}</Txt>
    </View>
  );
}

/** Bottom bar: the spoken action (tap = send now), a hint, the "..." button and End call. */
export function VoiceFooter({ action, hint, onMore }: { action: string; hint: string; onMore: () => void }) {
  const { c } = useTheme();
  const { endCall, sendNow, level, speaking } = useSpanner();
  return (
    <View style={[styles.footer, { backgroundColor: c.base }]}>
      <View style={{ flex: 1, flexDirection: "row", gap: 12 }}>
        <View style={{ width: 20, height: 48, justifyContent: "center", marginLeft: 0 }}>
          <Waveform active={!speaking && level > 0.15} barWidth={2} color={c.action} scale={20 / 24} />
        </View>
        <Pressable onPress={sendNow} accessibilityRole="button" style={{ flex: 1 }}>
          <View style={{ height: 48, justifyContent: "center" }}>
            <Txt weight="semibold" size={15} numberOfLines={1}>{action}</Txt>
          </View>
          <Txt size={10} color={c.textSecondary}>{hint}</Txt>
        </Pressable>
      </View>
      <Pressable onPress={onMore} accessibilityLabel="More" style={[styles.more, { backgroundColor: c.raised }]}>
        <Txt weight="semibold" size={18} color={c.textPrimary} style={{ textAlign: "center" }}>{"•••"}</Txt>
      </Pressable>
      <CallEndButton size={44} onPress={() => void endCall()} />
    </View>
  );
}

const styles = StyleSheet.create({
  topScrim: { position: "absolute", left: 0, right: 0, top: 0, height: 195 },
  bottomScrim: { position: "absolute", left: 0, right: 0, bottom: 0, height: 324 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", height: 36 },
  durationPill: { height: 32, borderRadius: radius.pill, backgroundColor: camera.surface, flexDirection: "row", alignItems: "center", gap: 9, paddingHorizontal: 12 },
  liveDot: { width: 7, height: 7, borderRadius: radius.pill },
  phaseBar: { height: 36, borderRadius: radius.r8, backgroundColor: camera.surface, justifyContent: "center", paddingHorizontal: 12 },
  userCard: { borderRadius: radius.r12, backgroundColor: camera.surface, padding: 16, paddingTop: 13 },
  finding: { minHeight: 108, borderRadius: radius.r12, backgroundColor: camera.surface, paddingHorizontal: 16, paddingTop: 13, paddingBottom: 16 },
  splitLabel: { position: "absolute", left: 12, top: 12, height: 24, borderRadius: radius.r6, backgroundColor: camera.surface, paddingHorizontal: 10, justifyContent: "center" },
  pipLabel: { position: "absolute", left: 6, right: 6, bottom: 6, height: 21, borderRadius: radius.r6, backgroundColor: camera.surface, alignItems: "center", justifyContent: "center" },
  stepCard: { borderRadius: radius.r12, padding: 16, gap: 12 },
  stepHeader: { flexDirection: "row", alignItems: "center", gap: 12 },
  track: { height: 4, borderRadius: radius.r2, overflow: "hidden" },
  fill: { height: 4, borderRadius: radius.r2 },
  hudBox: { position: "absolute", borderWidth: 2, borderRadius: radius.r12, padding: 8 },
  hudPill: { backgroundColor: camera.surface, borderRadius: radius.r8, padding: 8 },
  banner: { minHeight: 48, borderRadius: radius.r8, padding: 12, flexDirection: "row", alignItems: "center", gap: 12 },
  caption: { backgroundColor: camera.surface, borderRadius: radius.r12, padding: 16, gap: 8 },
  voiceStatus: { flexDirection: "row", alignItems: "center", gap: 8, minHeight: 24 },
  footer: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingHorizontal: 24, paddingTop: 12, paddingBottom: 8, height: 100 },
  more: { width: 44, height: 44, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
});

// Re-exported so screens use the same red as the call controls.
export { callFill };
