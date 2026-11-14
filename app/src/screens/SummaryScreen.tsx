import React from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { radius, useTheme } from "../theme";
import { useSpanner } from "../state/SpannerProvider";
import { absUrl } from "../lib/config";
import { Icon, icons, images, PrimaryCta, Txt } from "../components/ui";

const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export default function SummaryScreen() {
  const { c } = useTheme();
  const { summary, plan, media, dismissSummary } = useSpanner();
  if (!summary) return null;

  const total = Math.max(summary.steps_total, 1);
  const done = Math.min(summary.steps_done, total);
  const complete = done >= total;
  const object = (summary.object ?? plan?.object ?? "repair").toLowerCase();
  const photo = [...media].reverse().find((m) => m.kind === "illustration");
  const title = summary.fault ?? plan?.fault ?? "Repair finished";
  const lastNote = plan?.safety_notes?.[plan.safety_notes.length - 1] ?? "Before you use it, check it works as it should.";

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.base }}>
      <ScrollView contentContainerStyle={styles.body} bounces={false}>
        <View style={styles.top}>
          <Image source={images.logo} style={{ width: 160, height: 28, marginLeft: 10 }} contentFit="contain" />
          <Pressable onPress={dismissSummary} accessibilityLabel="Close" hitSlop={12}>
            <Icon src={icons.close} size={24} color={c.textSecondary} />
          </Pressable>
        </View>

        <Txt weight="semibold" size={11} color={c.action} style={{ marginTop: 28 }}>
          {complete ? "REPAIR COMPLETE" : "REPAIR ENDED"}
        </Txt>
        <View style={styles.headline}>
          <Txt weight="medium" size={64} lh={1.01} ls={-2.56}>{complete ? "You\nfixed it." : "Good\nprogress."}</Txt>
          <View style={[styles.seal, { backgroundColor: c.seal }]}>
            <Icon src={icons.completionCheck} size={48} color={c.onAction} />
          </View>
        </View>
        <Txt size={15} color={c.textSecondary} style={{ marginTop: 22 }}>
          {complete ? `Your ${object} is back where it belongs.` : `You got through ${done} of ${total} steps on your ${object}.`}
        </Txt>

        <View style={styles.bars}>
          {Array.from({ length: total }).map((_, i) => (
            <View key={i} style={{ flex: 1, height: 6, borderRadius: radius.r2, backgroundColor: i < done ? c.seal : c.borderSubtle }} />
          ))}
        </View>

        <View style={styles.stats}>
          <View style={{ flex: 1 }}>
            <Txt size={36} lh={1.1} ls={-1.44}>{mmss(summary.duration_s)}</Txt>
            <Txt size={12} color={c.textSecondary}>Repair time</Txt>
          </View>
          <View style={{ flex: 1 }}>
            <Txt size={36} lh={1.1} ls={-1.44}>{done} / {total}</Txt>
            <Txt size={12} color={c.textSecondary}>Steps completed</Txt>
          </View>
        </View>

        <View style={[styles.result, { backgroundColor: c.raised }]}>
          <Image source={photo ? { uri: absUrl(photo.url) } : images.repaired} style={styles.resultImg} contentFit="cover" />
          <View style={{ flex: 1, paddingVertical: 16, paddingRight: 16, gap: 8 }}>
            <Txt weight="semibold" size={18} numberOfLines={2}>{title}</Txt>
            {plan?.steps.length ? (
              <Txt size={12} color={c.textSecondary} numberOfLines={3}>
                {plan.steps.slice(-2).map((s) => s.expected_visual_state).filter(Boolean).join(". ")}
              </Txt>
            ) : null}
            <Txt weight="medium" size={10} color={c.textSecondary}>{object.toUpperCase()}</Txt>
          </View>
        </View>

        <View style={{ flex: 1, minHeight: 24 }} />
        <View style={[styles.rule, { backgroundColor: c.borderSubtle }]} />
        <Txt size={12} color={c.textSecondary}>{lastNote}</Txt>
        <View style={{ marginTop: 24 }}>
          <PrimaryCta label="Back to home" onPress={dismissSummary} />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  body: { flexGrow: 1, paddingHorizontal: 24, paddingTop: 20, paddingBottom: 24 },
  top: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  headline: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", marginTop: 12 },
  seal: { width: 104, height: 104, borderRadius: radius.pill, alignItems: "center", justifyContent: "center", marginTop: 14 },
  bars: { flexDirection: "row", gap: 4, marginTop: 28 },
  stats: { flexDirection: "row", marginTop: 22, gap: 24 },
  result: { height: 132, borderRadius: radius.r12, overflow: "hidden", flexDirection: "row", gap: 16, marginTop: 28 },
  resultImg: { width: 132, height: 132 },
  rule: { height: 1, marginBottom: 20 },
});
