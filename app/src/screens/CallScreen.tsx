import React, { useEffect, useRef, useState } from "react";
import { Alert as RNAlert, Pressable, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { CameraView, useCameraPermissions } from "expo-camera";
import { useKeepAwake } from "expo-keep-awake";
import * as Haptics from "expo-haptics";
import { useVideoPlayer, VideoView } from "expo-video";
import { camera, hud as hudColors, radius, useTheme } from "../theme";
import { useSpanner } from "../state/SpannerProvider";
import { absUrl } from "../lib/config";
import { Icon, icons, images, Txt } from "../components/ui";
import {
  CallHeader, CameraBackdrop, DurationPillStandalone, HudBoxes, PhaseBar, SafetyBanner, SpokenCaption, StepProgressCard, UserCard, VoiceFooter,
} from "../components/call";

const FRAME_MS = 1000; // contract: about one frame per second

const PHASE_TEXT = {
  identifying: "Looking · point the camera at the problem",
  diagnosing: "01 / Diagnose · show me what’s wrong",
  researching: "02 / Research · finding the fix",
} as const;

/** Everything on the live call: diagnose, guided repair, mistake intercept and the 3D / demonstration view. */
export default function CallScreen() {
  useKeepAwake();
  const { c, mode, toggle } = useTheme();
  const st = useSpanner();
  const [perm, requestPerm] = useCameraPermissions();
  const cam = useRef<CameraView>(null);
  const busy = useRef(false);

  useEffect(() => {
    if (perm && !perm.granted && perm.canAskAgain) void requestPerm();
  }, [perm, requestPerm]);

  // One JPEG per second, never while the previous capture is still running.
  useEffect(() => {
    if (!perm?.granted) return;
    const t = setInterval(async () => {
      if (busy.current || !cam.current) return;
      busy.current = true;
      try {
        const pic = await cam.current.takePictureAsync({ base64: true, quality: 0.4, skipProcessing: true, shutterSound: false });
        if (pic?.base64) st.sendFrame(pic.base64);
      } catch {
      } finally {
        busy.current = false;
      }
    }, FRAME_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [perm?.granted]);

  const warn = st.stepStatus === "mistake" || st.alert?.severity === "warn";
  useEffect(() => {
    if (warn) void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
  }, [warn]);

  const step = st.plan?.steps.find((s) => s.index === st.currentStep) ?? null;
  const guiding = st.phase === "guiding" || st.phase === "verifying";
  const clipUrl = step?.clip_url ?? [...st.media].reverse().find((m) => m.kind === "clip")?.url ?? null;
  const explainer = st.explainer && guiding;

  const openMore = () =>
    RNAlert.alert("Spanner", undefined, [
      ...(guiding ? [{ text: explainer ? "Back to camera" : "Show 3D view", onPress: () => st.setExplainer(!explainer) }] : []),
      { text: mode === "dark" ? "Light theme" : "Dark theme", onPress: toggle },
      { text: "Cancel", style: "cancel" as const },
    ]);

  const permitted = !!perm?.granted;

  if (explainer) {
    return (
      <View style={{ flex: 1, backgroundColor: c.base }}>
        <CameraBackdrop ref={cam} permitted={permitted} mode={clipUrl ? "pip" : "hidden"} pipTop={250} />
        <Explainer clipUrl={clipUrl} onMore={openMore} />
      </View>
    );
  }

  const caption =
    st.caption || (guiding ? "" : "Show me what’s going wrong.\nYou can just talk to me.");
  const footer = warn
    ? { action: "Say “I’ve fixed it”", hint: "Tap only once the area is clear" }
    : guiding
      ? { action: "Say “done”", hint: "“Repeat” · “show a diagram”" }
      : { action: "Say what’s going wrong", hint: "Tap once you’ve explained it" };

  return (
    <View style={{ flex: 1, backgroundColor: camera.surface }}>
      <CameraBackdrop ref={cam} permitted={permitted}>
        {guiding ? <HudBoxes warn={warn} /> : null}
      </CameraBackdrop>
      <SafeAreaView style={styles.fill} edges={["top"]} pointerEvents="box-none">
        <View style={styles.column} pointerEvents="box-none">
          <CallHeader />
          <View style={{ marginTop: 28, gap: 14 }}>
            {guiding ? (
              <StepProgressCard />
            ) : (
              <>
                <PhaseBar text={PHASE_TEXT[st.phase as keyof typeof PHASE_TEXT] ?? PHASE_TEXT.diagnosing} />
                {st.userText ? <UserCard text={st.userText} /> : null}
              </>
            )}
          </View>
          {!permitted && perm && !perm.canAskAgain ? (
            <Txt size={12} color={hudColors.warn} style={{ marginTop: 16 }}>Camera is off. Enable it in Settings so Spanner can see.</Txt>
          ) : null}
          {st.error ? <Txt size={11} color={hudColors.danger} style={{ marginTop: 10 }}>{st.error}</Txt> : null}

          <View style={{ flex: 1 }} pointerEvents="none" />

          {warn ? (
            <View style={{ marginBottom: 12 }}>
              <SafetyBanner severity="warn" message={st.alert?.message ?? "Pause — check the area is clear."} />
            </View>
          ) : null}
          {caption ? <SpokenCaption text={caption} speaking={st.speaking} listening={st.level > 0.15} /> : null}
        </View>
      </SafeAreaView>
      <SafeAreaView edges={["bottom"]} style={{ backgroundColor: c.base }}>
        <VoiceFooter action={footer.action} hint={footer.hint} onMore={openMore} />
      </SafeAreaView>
    </View>
  );
}

/** "See how it fits." (image / 3D) or "Watch it once." (clip) with Spanner talking over it. */
function Explainer({ clipUrl, onMore }: { clipUrl: string | null; onMore: () => void }) {
  const { c, mode } = useTheme();
  const st = useSpanner();
  const step = st.plan?.steps.find((s) => s.index === st.currentStep) ?? null;
  const illustration =
    [...st.media].reverse().find((m) => m.kind === "illustration" && (m.step_index == null || m.step_index === st.currentStep))?.url ??
    step?.illustration_url ??
    null;
  const labels = [...new Set([...st.scene3d.map((x) => x.part), ...(step?.parts ?? [])].filter((x): x is string => !!x))].slice(0, 2);

  return (
    <SafeAreaView style={styles.fill} edges={["top"]}>
      <View style={styles.column}>
        <View style={styles.exHeader}>
          <CallHeaderLogo />
          <DurationPillStandalone />
        </View>
        <Txt size={11} color={c.textSecondary} style={{ marginTop: 20 }}>
          {clipUrl ? "Demonstration · voice stays on" : "3D explanation · voice stays on"}
        </Txt>
        <Txt weight="medium" size={26} style={{ marginTop: 6 }}>{clipUrl ? "Watch it once." : "See how it fits."}</Txt>

        <View style={styles.stage}>
          {clipUrl ? (
            <ClipPlayer url={absUrl(clipUrl)} />
          ) : (
            <>
              <Image
                source={illustration ? { uri: absUrl(illustration) } : mode === "dark" ? images.model : images.modelLight}
                style={StyleSheet.absoluteFill}
                contentFit="contain"
              />
              {labels[0] ? <ModelLabel text={labels[0]} style={{ left: 0, top: 68 }} /> : null}
              {labels[1] ? <ModelLabel text={labels[1]} style={{ right: 0, bottom: 46 }} /> : null}
            </>
          )}
        </View>

        {st.caption ? <SpokenCaption text={st.caption} speaking={st.speaking} listening={st.level > 0.15} /> : null}
      </View>
      <SafeAreaView edges={["bottom"]} style={{ backgroundColor: c.base }}>
        <VoiceFooter action={"Say “let’s try it”"} hint={"“Show 3D” · “pause” · “repeat”"} onMore={onMore} />
      </SafeAreaView>
    </SafeAreaView>
  );
}

function CallHeaderLogo() {
  return <Image source={images.logo} style={{ width: 126, height: 22, marginLeft: 10 }} contentFit="contain" />;
}

function ModelLabel({ text, style }: { text: string; style: object }) {
  const { c } = useTheme();
  return (
    <View style={[{ position: "absolute", height: 30, borderRadius: radius.r8, backgroundColor: c.raised, paddingHorizontal: 12, justifyContent: "center" }, style]}>
      <Txt weight="medium" size={12}>{text}</Txt>
    </View>
  );
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Looping demonstration clip with the dark playback bar from the design. */
function ClipPlayer({ url }: { url: string }) {
  const player = useVideoPlayer(url, (p) => {
    p.loop = true;
    p.play();
  });
  const [playing, setPlaying] = useState(true);
  const [pos, setPos] = useState({ t: 0, d: 0 });
  useEffect(() => {
    const t = setInterval(() => setPos({ t: player.currentTime, d: player.duration }), 250);
    return () => clearInterval(t);
  }, [player]);
  const toggle = () => {
    if (playing) player.pause();
    else player.play();
    setPlaying(!playing);
  };
  return (
    <View style={StyleSheet.absoluteFill}>
      <VideoView player={player} style={StyleSheet.absoluteFill} contentFit="contain" nativeControls={false} />
      <View style={styles.playback}>
        <Pressable onPress={toggle} accessibilityLabel={playing ? "Pause" : "Play"} style={styles.playBtn}>
          {playing ? <Icon src={icons.pause} size={24} color={camera.text} /> : <Txt size={18} color={camera.text}>{"▶"}</Txt>}
        </Pressable>
        <View style={{ flex: 1 }}>
          <Txt weight="medium" size={10} color={camera.accent}>{playing ? "Playing · voice on" : "Paused"}</Txt>
          <View style={styles.pbTrack}>
            <View style={[styles.pbFill, { width: `${pos.d ? Math.min(100, (pos.t / pos.d) * 100) : 0}%` }]} />
          </View>
        </View>
        <Txt size={10} color={camera.text}>{fmt(pos.t)} / {fmt(pos.d)}</Txt>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  column: { flex: 1, paddingHorizontal: 24, paddingTop: 18, paddingBottom: 12 },
  exHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", height: 36 },
  stage: { flex: 1, marginHorizontal: -24, marginVertical: 12 },
  playback: { position: "absolute", left: 24, right: 24, bottom: 0, height: 48, borderRadius: radius.r12, backgroundColor: camera.surface, flexDirection: "row", alignItems: "center", gap: 10, paddingRight: 12 },
  playBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", marginLeft: 2 },
  pbTrack: { height: 3, borderRadius: radius.r2, backgroundColor: "#354137", marginTop: 8, overflow: "hidden" },
  pbFill: { height: 3, backgroundColor: camera.accent },
});
