import React, { useEffect, useRef } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, TextProps, View, ViewStyle } from "react-native";
import { Image } from "expo-image";
import { callFill, camera, font, radius, useTheme } from "../theme";

export const icons = {
  arrow: require("../../assets/icons/arrow.svg"),
  arrowLight: require("../../assets/icons/arrow-light.svg"),
  sun: require("../../assets/icons/sun.svg"),
  video: require("../../assets/icons/video.svg"),
  check: require("../../assets/icons/check.svg"),
  chev: require("../../assets/icons/chev.svg"),
  phone: require("../../assets/icons/phone.svg"),
  endCall: require("../../assets/icons/end-call.svg"),
  safetyDanger: require("../../assets/icons/safety-danger.svg"),
  safetyWarn: require("../../assets/icons/safety-warn.svg"),
  shield: require("../../assets/icons/shield.svg"),
  close: require("../../assets/icons/close.svg"),
  completionCheck: require("../../assets/icons/completion-check.svg"),
  pause: require("../../assets/icons/pause.svg"),
};

export const images = {
  logo: require("../../assets/img/logo.png"),
  homeCamera: require("../../assets/img/home-camera.png"),
  liveCamera: require("../../assets/img/live-camera.png"),
  repaired: require("../../assets/img/repaired-bicycle.png"),
  model: require("../../assets/img/model-render.png"),
  modelLight: require("../../assets/img/model-render-light.png"),
  pip: require("../../assets/img/pip-camera.png"),
};

export function Icon({ src, size, color }: { src: number; size: number; color?: string }) {
  return <Image source={src} style={{ width: size, height: size }} contentFit="contain" tintColor={color} />;
}

type Weight = "regular" | "medium" | "semibold";
export function Txt({
  size = 15,
  weight = "regular",
  color,
  lh = 1.35,
  ls,
  mono,
  style,
  ...rest
}: TextProps & { size?: number; weight?: Weight; color?: string; lh?: number; ls?: number; mono?: boolean }) {
  const { c } = useTheme();
  return (
    <Text
      {...rest}
      style={[
        {
          fontFamily: mono ? font.mono : font[weight],
          fontSize: size,
          lineHeight: Math.round(size * lh),
          color: color ?? c.textPrimary,
          letterSpacing: ls,
        },
        style,
      ]}
    />
  );
}

/** Lime (or danger) full-width action: label left, glyph right. */
export function PrimaryCta({
  label,
  onPress,
  variant = "primary",
  glyph,
  disabled,
}: {
  label: string;
  onPress?: () => void;
  variant?: "primary" | "danger";
  glyph?: number;
  disabled?: boolean;
}) {
  const { c } = useTheme();
  const danger = variant === "danger";
  const bg = danger ? c.dangerFg : c.action;
  const fg = danger ? c.onDanger : c.onAction;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [s.cta, { backgroundColor: bg, opacity: disabled ? 0.5 : pressed ? 0.85 : 1 }]}
    >
      <Txt weight="semibold" size={15} color={fg} style={{ flex: 1 }}>
        {label}
      </Txt>
      <Icon src={glyph ?? (danger ? icons.endCall : icons.arrow)} size={24} color={fg} />
    </Pressable>
  );
}

const WAVE = [6, 13, 20, 11, 6];
/** Five-bar voice waveform. Bars move while `active`. */
export function Waveform({ active = true, barWidth = 3, color = camera.accent, scale = 1 }: { active?: boolean; barWidth?: number; color?: string; scale?: number }) {
  const vals = useRef(WAVE.map(() => new Animated.Value(1))).current;
  useEffect(() => {
    if (!active) {
      vals.forEach((v) => v.setValue(1));
      return;
    }
    const loops = vals.map((v, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.timing(v, { toValue: 0.45, duration: 340 + i * 70, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
          Animated.timing(v, { toValue: 1, duration: 340 + i * 70, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        ]),
      ),
    );
    loops.forEach((l) => l.start());
    return () => loops.forEach((l) => l.stop());
  }, [active, vals]);
  return (
    <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 2, height: 24 * scale }}>
      {WAVE.map((h, i) => (
        <Animated.View
          key={i}
          style={{ width: barWidth, height: h * scale, borderRadius: radius.r2, backgroundColor: color, transform: [{ scaleY: vals[i] }] }}
        />
      ))}
    </View>
  );
}

export function CallEndButton({ size, onPress, style }: { size: number; onPress: () => void; style?: ViewStyle }) {
  return (
    <Pressable
      accessibilityLabel="End call"
      onPress={onPress}
      style={[{ width: size, height: size, borderRadius: radius.pill, backgroundColor: callFill, alignItems: "center", justifyContent: "center" }, style]}
    >
      <Icon src={icons.phone} size={24} color="#ffffff" />
    </Pressable>
  );
}

const s = StyleSheet.create({
  cta: { height: 56, borderRadius: radius.r8, padding: 16, flexDirection: "row", alignItems: "center", gap: 8 },
});
