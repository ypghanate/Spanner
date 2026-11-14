import React from "react";
import { ActivityIndicator, Pressable, ScrollView, Text, View } from "react-native";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { useFonts, Inter_400Regular, Inter_500Medium, Inter_600SemiBold } from "@expo-google-fonts/inter";
import { JetBrainsMono_400Regular } from "@expo-google-fonts/jetbrains-mono";
import { ThemeProvider, useTheme } from "./src/theme";
import { SpannerProvider, useSpanner } from "./src/state/SpannerProvider";
import HomeScreen from "./src/screens/HomeScreen";
import CallScreen from "./src/screens/CallScreen";
import SafetyScreen from "./src/screens/SafetyScreen";
import SummaryScreen from "./src/screens/SummaryScreen";

/** Shows a JS crash on screen (with the message to send us) instead of the app just closing. */
class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Spanner crashed:", error, info.componentStack);
  }
  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <View style={{ flex: 1, backgroundColor: "#101512", padding: 24, paddingTop: 80 }}>
        <Text style={{ color: "#f3917c", fontSize: 20, fontWeight: "600" }}>Spanner hit an error</Text>
        <ScrollView style={{ marginTop: 16 }}>
          <Text selectable style={{ color: "#f2f2e9", fontSize: 12 }}>{String(error.stack ?? error.message)}</Text>
        </ScrollView>
        <Pressable onPress={() => this.setState({ error: null })} style={{ backgroundColor: "#caf879", padding: 16, borderRadius: 8, marginTop: 16 }}>
          <Text style={{ color: "#172415", fontWeight: "600" }}>Try again</Text>
        </Pressable>
      </View>
    );
  }
}

function Router() {
  const { status, phase, summary } = useSpanner();
  const { mode } = useTheme();
  const inCall = status === "live";

  let screen: React.ReactNode;
  if (summary) screen = <SummaryScreen />;
  else if (!inCall) screen = <HomeScreen />;
  else if (phase === "safety_stop") screen = <SafetyScreen />;
  else screen = <CallScreen />;

  // The call screen sits on a camera feed, so its status bar is always light.
  const light = inCall && phase !== "safety_stop";
  return (
    <>
      <StatusBar style={light ? "light" : mode === "dark" ? "light" : "dark"} />
      {screen}
    </>
  );
}

export default function App() {
  const [loaded] = useFonts({ Inter_400Regular, Inter_500Medium, Inter_600SemiBold, JetBrainsMono_400Regular });
  if (!loaded) {
    return (
      <View style={{ flex: 1, backgroundColor: "#101512", alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color="#caf879" />
      </View>
    );
  }
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <ErrorBoundary>
          <SpannerProvider>
            <Router />
          </SpannerProvider>
        </ErrorBoundary>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
