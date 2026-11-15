import React from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { radius, useTheme } from "../theme";
import { useSpanner } from "../state/SpannerProvider";
import { Icon, icons, images, PrimaryCta, Txt } from "../components/ui";


export default function HomeScreen() {
  const { c, toggle } = useTheme();
  const { openScanner, history } = useSpanner();

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.base }}>
      <View style={styles.body}>
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

        {/* The question sits in the middle of the screen, with the call button right under it. */}
        <View style={styles.middle}>
          <Txt weight="medium" size={60} lh={1.03} ls={-2.4} style={{ textAlign: "center" }}>
            {"What needs\nfixing?"}
          </Txt>
          <Txt size={15} color={c.textSecondary} lh={1.3} style={{ marginTop: 16, textAlign: "center" }}>
            Show Spanner what’s broken.
          </Txt>
          <View style={styles.cta}>
            {/* Starting a call pairs this phone with the laptop webapp: the QR scanner opens first. */}
            <PrimaryCta label="Start video call" onPress={openScanner} />
          </View>
        </View>

        {/* The two latest repairs, kept at the bottom. */}
        <View>
          <Txt mono size={10} color={c.textSecondary} lh={1.3} style={{ marginBottom: 14 }}>RECENT REPAIRS</Txt>
          {history.slice(0, 2).map((h) => (
            <View key={h.id} style={[styles.row, { borderTopColor: c.borderSubtle }]}>
              <Icon src={icons.check} size={20} color={c.action} />
              <View style={{ flex: 1 }}>
                <Txt weight="medium" size={15} lh={1.3} numberOfLines={1}>{h.title}</Txt>
                <Txt size={11} color={c.textSecondary} lh={1.3} numberOfLines={1} style={{ marginTop: 2 }}>{h.detail}</Txt>
              </View>
              <Icon src={icons.chev} size={18} color={c.textSecondary} />
            </View>
          ))}
        </View>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  body: { flex: 1, paddingHorizontal: 24, paddingTop: 20, paddingBottom: 16 },
  brand: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingLeft: 12 },
  theme: { width: 44, height: 44, borderRadius: radius.pill, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  middle: { flex: 1, alignItems: "center", justifyContent: "center" },
  cta: { alignSelf: "stretch", marginTop: 32 },
  row: { flexDirection: "row", alignItems: "center", gap: 14, height: 73, borderTopWidth: 1, paddingLeft: 2 },
});
