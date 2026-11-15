import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { CameraView, useCameraPermissions } from "expo-camera";
import { camera, radius, useTheme } from "../theme";
import { useSpanner } from "../state/SpannerProvider";
import { Icon, icons, Txt } from "../components/ui";

const FRAME = 248;

/** Scan the QR the laptop viewer shows ("SCAN WITH THE PHONE") to pair this phone as its camera. */
export default function PairScreen() {
  const { c } = useTheme();
  const { pair, closeScanner, status, error } = useSpanner();
  const [perm, requestPerm] = useCameraPermissions();
  const [localError, setLocalError] = useState<string | null>(null);
  const handling = useRef(false);
  const connecting = status === "connecting";

  useEffect(() => {
    if (perm && !perm.granted && perm.canAskAgain) void requestPerm();
  }, [perm, requestPerm]);

  const onScan = async ({ data }: { data: string }) => {
    if (handling.current || connecting) return;
    handling.current = true;
    setLocalError(null);
    const err = await pair(data);
    if (err) {
      setLocalError(err);
      setTimeout(() => (handling.current = false), 2500); // don't re-fire on the same code at once
    }
  };

  const message = localError ?? error;

  return (
    <View style={{ flex: 1, backgroundColor: camera.surface }}>
      {perm?.granted ? (
        <CameraView
          style={StyleSheet.absoluteFill}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={connecting ? undefined : onScan}
        />
      ) : null}
      <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: camera.tint }]} />

      <SafeAreaView style={styles.fill}>
        <View style={styles.top}>
          <Txt weight="semibold" size={18} color={camera.text}>Pair with laptop</Txt>
          <Pressable onPress={closeScanner} accessibilityLabel="Cancel" hitSlop={12} style={styles.close}>
            <Icon src={icons.close} size={24} color={camera.text} />
          </Pressable>
        </View>

        <View style={styles.middle} pointerEvents="none">
          <View style={[styles.frame, { borderColor: message ? "#f3917c" : camera.accent }]} />
        </View>

        <View style={[styles.card, { backgroundColor: camera.surface }]}>
          {connecting ? (
            <View style={styles.row}>
              <ActivityIndicator color={camera.accent} />
              <Txt weight="medium" size={15} color={camera.text}>Connecting to your laptop…</Txt>
            </View>
          ) : (
            <>
              <Txt weight="medium" size={10} color={camera.accent}>STEP 1 · ON YOUR LAPTOP</Txt>
              <Txt weight="medium" size={18} color={camera.text} style={{ marginTop: 8 }}>Open the Spanner page and say “use my phone”.</Txt>
              <Txt size={12} color={c.textSecondary} style={{ marginTop: 8 }}>Then point this camera at the QR code on its screen.</Txt>
            </>
          )}
          {!perm?.granted && perm && !perm.canAskAgain ? (
            <Txt size={12} color="#ffd166" style={{ marginTop: 12 }}>Camera is off. Enable it in Settings so Spanner can scan.</Txt>
          ) : null}
          {message ? <Txt size={12} color="#f3917c" style={{ marginTop: 12 }}>{message}</Txt> : null}
        </View>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, paddingHorizontal: 24, paddingBottom: 24 },
  top: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", height: 56, marginTop: 8 },
  close: { width: 44, height: 44, borderRadius: radius.pill, backgroundColor: camera.surface, alignItems: "center", justifyContent: "center" },
  middle: { flex: 1, alignItems: "center", justifyContent: "center" },
  frame: { width: FRAME, height: FRAME, borderRadius: radius.r12, borderWidth: 3 },
  card: { borderRadius: radius.r12, padding: 16 },
  row: { flexDirection: "row", alignItems: "center", gap: 12 },
});
