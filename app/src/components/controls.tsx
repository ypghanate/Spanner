import React, { useEffect, useRef, useState } from "react";
import { Animated, Easing, Modal, Pressable, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { radius, scrim, useTheme } from "../theme";
import { CallView, useSpanner } from "../state/SpannerProvider";
import { CallEndButton, Icon, icons, Txt } from "./ui";

/** Which extras the sheet carries: nothing, "Keep diagnosing", or the diagram choices. */
export type SheetVariant = "basic" | "diagnosis" | "repair";

const DIAGRAMS: { label: string; view: CallView }[] = [
  { label: "3D view", view: "3d" },
  { label: "2D guide", view: "2d" },
  { label: "Side by side", view: "split" },
  { label: "Watch demo", view: "demo" },
];

function Control({
  label,
  icon,
  bg,
  fg,
  onPress,
}: {
  label: string;
  icon: number;
  bg: string;
  fg: string;
  onPress: () => void;
}) {
  const { c } = useTheme();
  return (
    <View style={styles.control}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        onPress={onPress}
        style={({ pressed }) => [styles.controlBtn, { backgroundColor: bg, opacity: pressed ? 0.85 : 1 }]}
      >
        <Icon src={icon} size={24} color={fg} />
      </Pressable>
      <Txt size={11} color={c.textSecondary} numberOfLines={1} style={{ textAlign: "center" }}>
        {label}
      </Txt>
    </View>
  );
}

/** "Call controls" bottom sheet opened from the ••• button in the call footer. */
export function ControlsSheet({ visible, variant, onClose }: { visible: boolean; variant: SheetVariant; onClose: () => void }) {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  const st = useSpanner();
  const [shown, setShown] = useState(visible);
  const t = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      setShown(true);
      Animated.timing(t, { toValue: 1, duration: 220, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
    } else {
      Animated.timing(t, { toValue: 0, duration: 160, easing: Easing.in(Easing.cubic), useNativeDriver: true }).start(({ finished }) => {
        if (finished) setShown(false);
      });
    }
  }, [visible, t]);

  if (!shown) return null;

  const endCall = () => {
    onClose();
    void st.endCall();
  };
  const pick = (view: CallView) => {
    st.setView(view);
    onClose();
  };

  // Dark pill = off / muted, so a switched-off control is obvious at a glance.
  const off = { bg: c.dangerSurface, fg: c.dangerFg };
  const on = { bg: c.raised, fg: c.textPrimary };

  return (
    <Modal transparent visible animationType="none" onRequestClose={onClose} statusBarTranslucent>
      <View style={styles.root}>
        <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: scrim, opacity: t }]}>
          <Pressable style={StyleSheet.absoluteFill} onPress={onClose} accessibilityLabel="Close call controls" />
        </Animated.View>

        <Animated.View
          style={[
            styles.sheet,
            {
              backgroundColor: c.base,
              paddingBottom: 24 + insets.bottom,
              transform: [{ translateY: t.interpolate({ inputRange: [0, 1], outputRange: [360, 0] }) }],
            },
          ]}
        >
          <View style={styles.header}>
            <Txt weight="semibold" size={18}>Call controls</Txt>
            <Pressable onPress={onClose} accessibilityLabel="Close" style={styles.close}>
              <Txt weight="semibold" size={18} color={c.textSecondary}>×</Txt>
            </Pressable>
          </View>
          <Txt size={12} color={c.textSecondary}>Voice stays on unless you mute it.</Txt>

          <View style={styles.row}>
            <Control
              label={st.micMuted ? "Unmute" : "Mute"}
              icon={icons.mic}
              bg={st.micMuted ? off.bg : on.bg}
              fg={st.micMuted ? off.fg : on.fg}
              onPress={() => st.setMicMuted(!st.micMuted)}
            />
            <Control
              label="Speaker"
              icon={icons.speaker}
              bg={st.speakerOn ? c.action : on.bg}
              fg={st.speakerOn ? c.onAction : on.fg}
              onPress={() => st.setSpeakerOn(!st.speakerOn)}
            />
            <Control
              label="Camera"
              icon={icons.camera}
              bg={st.cameraOn ? on.bg : off.bg}
              fg={st.cameraOn ? on.fg : off.fg}
              onPress={() => st.setCameraOn(!st.cameraOn)}
            />
            <View style={styles.control}>
              <CallEndButton size={56} onPress={endCall} />
              <Txt size={11} color={c.textSecondary} numberOfLines={1} style={{ textAlign: "center" }}>End call</Txt>
            </View>
          </View>

          {variant !== "basic" ? <View style={[styles.rule, { backgroundColor: c.borderSubtle }]} /> : null}

          {variant === "diagnosis" ? (
            <Pressable onPress={onClose} accessibilityRole="button" style={styles.linkRow}>
              <Txt weight="medium" size={15}>Keep diagnosing</Txt>
              <Txt weight="semibold" size={18} color={c.textSecondary}>→</Txt>
            </Pressable>
          ) : null}

          {variant === "repair" ? (
            <>
              <Txt weight="semibold" size={18}>Show a diagram</Txt>
              <View style={styles.grid}>
                {DIAGRAMS.map((d) => (
                  <Pressable
                    key={d.view}
                    accessibilityRole="button"
                    onPress={() => pick(d.view)}
                    style={({ pressed }) => [styles.choice, { backgroundColor: c.raised, opacity: pressed ? 0.85 : 1 }]}
                  >
                    <Txt weight="medium" size={12}>{d.label}</Txt>
                  </Pressable>
                ))}
              </View>
              {st.view !== "camera" ? (
                <Pressable onPress={() => pick("camera")} accessibilityRole="button" style={styles.linkRow}>
                  <Txt weight="medium" size={15}>Back to camera</Txt>
                  <Txt weight="semibold" size={18} color={c.textSecondary}>→</Txt>
                </Pressable>
              ) : null}
            </>
          ) : null}
        </Animated.View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: "flex-end" },
  sheet: { borderTopLeftRadius: radius.r16, borderTopRightRadius: radius.r16, paddingHorizontal: 24, paddingTop: 24, gap: 16 },
  header: { height: 44, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  close: { width: 44, height: 44, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  control: { width: 56, gap: 8, alignItems: "center" },
  controlBtn: { width: 56, height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  rule: { height: 1 },
  linkRow: { height: 48, flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  grid: { flexDirection: "row", flexWrap: "wrap", rowGap: 12, columnGap: 16 },
  choice: { width: "47.7%", flexGrow: 1, height: 56, borderRadius: radius.r8, paddingHorizontal: 12, justifyContent: "center" },
});
