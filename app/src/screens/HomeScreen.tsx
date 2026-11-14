import React from "react";
import { Pressable, ScrollView, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { camera, radius, useTheme } from "../theme";
import { useSpanner } from "../state/SpannerProvider";
import { Icon, icons, images, PrimaryCta, Txt } from "../components/ui";


export default function HomeScreen() {
  const { c, toggle } = useTheme();
  const { openScanner, history } = useSpanner();

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.base }}>
      <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
        <View style={styles.brand}>
          <Image source={images.logo} style={{ width: 181, height: 32 }} contentFit="contain" />
          <Pressable
            onPress={toggle}
            accessibilityLabel="Switch theme"
            style={[styles.theme, { borderColor: c.borderSubtle }]}
          >
            <Icon src={icons.sun} size={22} color={c.textPrimary} />
          </Pressable>
        </View>

        <Txt weight="medium" size={49} lh={1.03} ls={-1.96} style={{ marginTop: 32 }}>
          {"What needs\nfixing?"}
        </Txt>
        <Txt size={15} color={c.textSecondary} lh={1.3} style={{ marginTop: 14 }}>
          Show Spanner what’s broken.
        </Txt>

        <View style={styles.preview}>
          <Image source={images.homeCamera} style={StyleSheet.absoluteFill} contentFit="cover" />
          <View style={styles.pill}>
            <Icon src={icons.video} size={16} color={camera.accent} />
            <Txt weight="medium" size={10} color={camera.accent}>Camera + voice</Txt>
          </View>
        </View>

        <View style={{ marginTop: 20 }}>
          {/* Starting a call pairs this phone with the laptop webapp: the QR scanner opens first. */}
          <PrimaryCta label="Start video call" onPress={openScanner} />
        </View>

        <View style={styles.recentHead}>
          <Txt mono size={10} color={c.textSecondary} lh={1.3}>RECENT REPAIRS</Txt>
          <Txt weight="medium" size={12}>View all</Txt>
        </View>

        {history.map((h) => (
          <View key={h.id} style={[styles.row, { borderTopColor: c.borderSubtle }]}>
            <Icon src={icons.check} size={20} color={c.action} />
            <View style={{ flex: 1 }}>
              <Txt weight="medium" size={15} lh={1.3} numberOfLines={1}>{h.title}</Txt>
              <Txt size={11} color={c.textSecondary} lh={1.3} numberOfLines={1} style={{ marginTop: 2 }}>{h.detail}</Txt>
            </View>
            <Icon src={icons.chev} size={18} color={c.textSecondary} />
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: 24, paddingTop: 20, paddingBottom: 32 },
  brand: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingLeft: 12 },
  theme: { width: 44, height: 44, borderRadius: radius.pill, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  preview: { height: 218, borderRadius: radius.r6, overflow: "hidden", marginTop: 32, backgroundColor: camera.surface },
  pill: {
    position: "absolute", left: 14, top: 14, height: 28, borderRadius: radius.pill, backgroundColor: camera.surface,
    flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 10,
  },
  recentHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginTop: 40, marginBottom: 14, paddingRight: 14 },
  row: { flexDirection: "row", alignItems: "center", gap: 14, height: 73, borderTopWidth: 1, paddingLeft: 2 },
});
