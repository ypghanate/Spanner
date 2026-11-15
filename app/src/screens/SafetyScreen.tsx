import React from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { useTheme } from "../theme";
import { useSpanner } from "../state/SpannerProvider";
import { Icon, icons, images, PrimaryCta, Txt } from "../components/ui";
import { SafetyBanner, Transcript } from "../components/call";

/** Phase safety_stop: mains / gas. Spanner refuses to guide and tells the user what to do. */
export default function SafetyScreen() {
  const { c } = useTheme();
  const { endCall, alert, plan } = useSpanner();
  const banner = alert?.message ?? "Repair paused · mains electricity detected";
  const advice = plan?.safety_notes?.[0] ?? "Keep clear of exposed wiring and keep other people away from the area.";

  const startDifferent = async () => {
    await endCall();
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.base }}>
      <ScrollView contentContainerStyle={styles.body} bounces={false}>
        <Image source={images.logo} style={{ width: 160, height: 28, marginLeft: 10 }} contentFit="contain" />
        <View style={{ marginTop: 32 }}>
          <SafetyBanner severity="danger" message={banner} />
        </View>
        <View style={{ marginTop: 36 }}>
          <Icon src={icons.shield} size={34} color={c.dangerFg} />
        </View>
        <Txt weight="medium" size={40} lh={1.07} ls={-1.6} style={{ marginTop: 22 }}>
          {"This needs\nan electrician."}
        </Txt>
        <Txt size={16} color={c.textSecondary} style={{ marginTop: 16 }}>
          I can’t guide repairs on mains-powered wiring.
        </Txt>
        <View style={[styles.rule, { backgroundColor: c.borderSubtle }]} />
        <Txt size={14}>{advice}</Txt>

        <View style={{ flex: 1, minHeight: 32 }} />
        <Transcript />
        <View style={{ marginTop: 20 }}>
          <PrimaryCta variant="danger" label="End call" onPress={() => void endCall()} />
        </View>
        <Pressable onPress={startDifferent} style={styles.different} accessibilityRole="button">
          <Txt weight="medium" size={13} style={{ flex: 1 }}>Start a different repair</Txt>
          <Icon src={icons.arrowLight} size={22} color={c.textPrimary} />
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  body: { flexGrow: 1, paddingHorizontal: 24, paddingTop: 20, paddingBottom: 24 },
  rule: { height: 1, marginVertical: 24, marginTop: 40 },
  different: { flexDirection: "row", alignItems: "center", height: 35, marginTop: 14 },
});
