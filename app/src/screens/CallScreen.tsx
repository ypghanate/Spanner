import React, { useEffect, useRef, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Image } from "expo-image";
import { CameraView, useCameraPermissions } from "expo-camera";
import { useKeepAwake } from "expo-keep-awake";
import * as Haptics from "expo-haptics";
import * as FileSystem from "expo-file-system/legacy";
import { useVideoPlayer, VideoView } from "expo-video";
import type { RepairStep } from "../../types/contracts";
import { camera, hud as hudColors, radius, useTheme } from "../theme";
import { CallView, useSpanner } from "../state/SpannerProvider";
import { absUrl } from "../lib/config";
import { Icon, icons, images, Txt } from "../components/ui";
import {
  CallHeader, CameraBackdrop, CameraBox, DurationPillStandalone, FindingCard, HudBoxes, SafetyBanner, StepProgressCard, Transcript, VoiceFooter,
} from "../components/call";
import { ControlsSheet, SheetVariant } from "../components/controls";
import { Diagram2D } from "../components/diagram";

const CAPTURE_GAP_MS = 400; // pause between photos; the provider throttles the upload to about one coach frame per 1.2 s

/** Everything on the live call: diagnose, guided repair, mistake intercept and the diagram views. The phone is the laptop's camera. */
export default function CallScreen() {
  useKeepAwake();
  const { c } = useTheme();
  const st = useSpanner();
  const [perm, requestPerm] = useCameraPermissions();
  const cam = useRef<CameraView>(null);
  const send = useRef(st.sendCapture);
  send.current = st.sendCapture;
  const [sheet, setSheet] = useState(false);
  const [capErr, setCapErr] = useState<string | null>(null);

  useEffect(() => {
    if (perm && !perm.granted && perm.canAskAgain) void requestPerm();
  }, [perm, requestPerm]);

  // Photos for the laptop: one at a time (the camera can't take two at once), never while it is switched off.
  useEffect(() => {
    if (!perm?.granted) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    let fails = 0;
    const loop = async () => {
      if (stop) return;
      let gap = CAPTURE_GAP_MS;
      if (cam.current) {
        try {
          // a camera call that never settles (app backgrounded, view swapped) must not stall the loop for good
          const shoot = (opts: { quality: number; shutterSound?: boolean; skipProcessing?: boolean }) =>
            Promise.race([
              cam.current!.takePictureAsync(opts),
              new Promise<never>((_, no) => setTimeout(() => no(new Error("camera timeout (is the camera ready?)")), 4000)),
            ]);
          // no skipProcessing: it discards `quality`, which sent multi-MB photos several times a second
          const pic = await shoot({ quality: 0.4, shutterSound: false }).catch(() => shoot({ quality: 0.4 }));
          fails = 0;
          setCapErr(null);
          // photos nothing uploaded are deleted at once so the cache does not fill up
          if (pic?.uri && !stop && !send.current(pic.uri)) void FileSystem.deleteAsync(pic.uri, { idempotent: true }).catch(() => {});
        } catch (e) {
          setCapErr(e instanceof Error ? e.message : String(e));
          gap = Math.min(3000, 300 * 2 ** Math.min(fails++, 4)); // back off instead of retrying every 120 ms
        }
      }
      else if (!cam.current) setCapErr("camera view not mounted");
      if (!stop) timer = setTimeout(loop, gap);
    };
    timer = setTimeout(loop, 500); // let the camera warm up
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [perm?.granted]);

  const warn = st.stepStatus === "mistake" || st.alert?.severity === "warn";
  useEffect(() => {
    if (warn) void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {});
  }, [warn]);

  const step = st.plan?.steps.find((s) => s.index === st.currentStep) ?? null;
  const guiding = st.phase === "guiding" || st.phase === "verifying";
  const clipUrl = step?.clip_url ?? [...st.media].reverse().find((m) => m.kind === "clip")?.url ?? null;
  const view: CallView = guiding ? st.view : "camera";
  const understood = st.phase === "researching" && !!st.finding;
  const permitted = !!perm?.granted;

  const variant: SheetVariant = guiding ? "repair" : st.phase === "diagnosing" || st.phase === "researching" ? "diagnosis" : "basic";
  const openMore = () => setSheet(true);
  const controls = <ControlsSheet visible={sheet} variant={variant} onClose={() => setSheet(false)} />;

  if (view !== "camera") {
    return (
      <View style={{ flex: 1, backgroundColor: c.base }}>
        {view === "split" || view === "demo" ? null : <CameraBackdrop ref={cam} permitted={permitted} mode="hidden" />}
        {/* a tap anywhere cuts Spanner off and listens */}
        <Pressable style={{ flex: 1 }} onPress={st.tapScreen} accessible={false}>
          <Explainer view={view} cam={cam} permitted={permitted} clipUrl={clipUrl} onMore={openMore} />
        </Pressable>
        {controls}
      </View>
    );
  }

  // the bottom line: what to do next, or STOP when something is wrong
  const footer = warn
    ? { action: "STOP", hint: st.alert?.message ?? "Tap only once the area is clear" }
    : guiding
      ? { action: "Show me what’s going on", hint: "Say “done” · “repeat” · “show a diagram”" }
      : understood
        ? { action: "Show me what’s going on", hint: "Say “show me how” · or ask a question" }
        : { action: "Show me what’s going on", hint: "Tap once you’ve explained it" };

  return (
    <View style={{ flex: 1, backgroundColor: camera.surface }}>
      <CameraBackdrop ref={cam} permitted={permitted}>
        {guiding || st.hud.length ? <HudBoxes warn={warn} /> : null}
        {/* a tap anywhere cuts Spanner off and listens; if Spanner is quiet it sends what was said */}
        <Pressable style={StyleSheet.absoluteFill} onPress={st.tapScreen} accessible={false} />
      </CameraBackdrop>
      <SafeAreaView style={styles.fill} edges={["top"]} pointerEvents="box-none">
        <View style={styles.column} pointerEvents="box-none">
          <CallHeader />
          {capErr || st.error ? (
            <Txt size={11} color="#FF3D5A" style={{ marginTop: 8 }}>{capErr ? "Camera: " + capErr : st.error}</Txt>
          ) : null}
          <View style={{ marginTop: 28, gap: 14 }}>
            {guiding ? (
              <StepProgressCard />
            ) : (
              understood && st.finding ? <FindingCard title={st.finding.title} detail={st.finding.detail} /> : null
            )}
          </View>
          {!permitted && perm && !perm.canAskAgain ? (
            <Txt size={12} color={hudColors.warn} style={{ marginTop: 16 }}>Camera is off. Enable it in Settings so Spanner can see.</Txt>
          ) : null}
          {!st.connected ? (
            <Txt size={11} color={hudColors.warn} style={{ marginTop: 10 }}>Lost contact with your laptop. Reconnecting…</Txt>
          ) : null}

          <View style={{ flex: 1 }} pointerEvents="none" />

          {warn ? (
            <View style={{ marginBottom: 12 }}>
              <SafetyBanner severity="warn" message={st.alert?.message ?? "Pause — check the area is clear."} />
            </View>
          ) : null}
          <Transcript />
        </View>
      </SafeAreaView>
      <SafeAreaView edges={["bottom"]} style={{ backgroundColor: c.base }}>
        <VoiceFooter action={footer.action} hint={footer.hint} onMore={openMore} />
      </SafeAreaView>
      {controls}
    </View>
  );
}

const sentences = (t: string) => (t.match(/[^.!?]+[.!?]?/g) ?? []).map((s) => s.trim().replace(/[.!?]$/, "")).filter(Boolean);

const COPY: Record<Exclude<CallView, "camera">, { label: string; title: string }> = {
  "3d": { label: "3D explanation · voice stays on", title: "See how it fits." },
  "2d": { label: "2D guide · voice stays on", title: "Follow the chain path." },
  split: { label: "Camera + guide · voice stays on", title: "Your repair, side by side." },
  demo: { label: "Demonstration · voice stays on", title: "Watch it once." },
};

/** 3D view, 2D step board, side-by-side guide and demonstration player, with Spanner talking over them. */
function Explainer({
  view,
  cam,
  permitted,
  clipUrl,
  onMore,
}: {
  view: Exclude<CallView, "camera">;
  cam: React.RefObject<CameraView | null>;
  permitted: boolean;
  clipUrl: string | null;
  onMore: () => void;
}) {
  const { c, mode } = useTheme();
  const st = useSpanner();
  const step = st.plan?.steps.find((s) => s.index === st.currentStep) ?? null;
  const copy = COPY[view];
  const object = st.plan?.object ? shortName(st.plan.object) : null;
  const title = view === "split" && object ? `Your ${object}, side by side.` : copy.title;
  const render =
    [...st.media].reverse().find((m) => m.kind === "illustration" && (m.step_index == null || m.step_index === st.currentStep))?.url ??
    step?.illustration_url ??
    null;
  const steps = step ? sentences(step.instruction) : [];

  return (
    <SafeAreaView style={styles.fill} edges={["top"]}>
      <View style={styles.column}>
        <View style={[styles.exHeader, { justifyContent: "flex-end" }]}>
          <DurationPillStandalone />
        </View>
        <Txt size={11} color={c.textSecondary} style={{ marginTop: 19 }}>{copy.label}</Txt>
        <Txt weight="medium" size={26} numberOfLines={1} adjustsFontSizeToFit style={{ marginTop: 19 }}>{title}</Txt>

        <View style={styles.stage}>
          {view === "3d" || (view === "demo" && !clipUrl) ? (
            <ModelRender uri={render ? absUrl(render) : null} dark={mode === "dark"} />
          ) : null}

          {view === "3d" ? <Labels step={step} /> : null}

          {view === "demo" ? (
            <>
              {clipUrl ? <ClipPlayer url={absUrl(clipUrl)} /> : null}
              <CameraBox ref={cam} permitted={permitted} variant="pip" style={styles.pip} />
            </>
          ) : null}

          {view === "2d" ? (
            <>
              <View style={styles.board}>
                <Diagram2D width={354} />
              </View>
              <StepChip text={steps.length ? `1 · ${steps[0]}` : "1 · Lift the chain"} style={{ left: 24, top: 24, minWidth: 141 }} />
              {steps.length === 0 || steps.length > 1 ? (
                <StepChip text={steps.length ? `2 · ${steps[1]}` : "2 · Rest it on the top teeth"} style={{ right: 24, bottom: 8, minWidth: 196 }} />
              ) : null}
            </>
          ) : null}

          {view === "split" ? (
            <View style={styles.split}>
              <CameraBox ref={cam} permitted={permitted} variant="split" style={styles.splitCamera} />
              <View style={[styles.splitPanel, { backgroundColor: c.raised }]}>
                <Txt weight="medium" size={10} color={c.textSecondary} style={{ position: "absolute", left: 16, top: 20 }}>2D GUIDE</Txt>
                <Txt weight="medium" size={15} numberOfLines={4} style={{ position: "absolute", left: 16, top: 54, width: 154 }}>
                  {steps.length ? steps[0] + "." : "Lay the chain on the top teeth."}
                </Txt>
                <View style={{ position: "absolute", left: 174, top: 20 }}>
                  <Diagram2D width={164} />
                </View>
              </View>
            </View>
          ) : null}
        </View>

        <Transcript />
      </View>
      <SafeAreaView edges={["bottom"]} style={{ backgroundColor: c.base }}>
        <VoiceFooter action={"Say “let’s try it”"} hint={"“Show 3D” · “pause” · “repeat”"} onMore={onMore} />
      </SafeAreaView>
    </SafeAreaView>
  );
}

/** "Royal Kludge RK61 60% mechanical keyboard (hot-swappable)" -> "keyboard" */
const shortName = (o: string) => {
  const base = o.replace(/\s*\(.*\)\s*$/, "").trim().split(/\s+/);
  return (base[base.length - 1] ?? o).toLowerCase();
};

/** Part ids from the plan's 3D commands read nicely: "switch_F" -> "Switch F". */
const partLabel = (p: string) => {
  const t = p.replace(/_/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
};

function ModelRender({ uri, dark }: { uri: string | null; dark: boolean }) {
  return <Image source={uri ? { uri } : dark ? images.model : images.modelLight} style={StyleSheet.absoluteFill} contentFit="contain" />;
}

/** Part tags on the 3D render, from the laptop plan's 3D commands for this step. */
function Labels({ step }: { step: RepairStep | null }) {
  const st = useSpanner();
  const labels = [...new Set([...st.scene3d.map((x) => x.part), ...(step?.parts ?? [])].filter((x): x is string => !!x))].slice(0, 2).map(partLabel);
  return (
    <>
      {labels[0] ? <Tag text={labels[0]} style={{ left: 24, top: 68 }} /> : null}
      {labels[1] ? <Tag text={labels[1]} style={{ right: 24, bottom: 16 }} /> : null}
    </>
  );
}

function Tag({ text, style }: { text: string; style: object }) {
  const { c } = useTheme();
  return (
    <View style={[{ position: "absolute", height: 30, borderRadius: radius.r8, backgroundColor: c.raised, paddingHorizontal: 12, justifyContent: "center" }, style]}>
      <Txt weight="medium" size={12}>{text}</Txt>
    </View>
  );
}

function StepChip({ text, style }: { text: string; style: object }) {
  return <Tag text={text.length > 34 ? text.slice(0, 33).trimEnd() + "…" : text} style={style} />;
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
    <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
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
  stage: { flex: 1, marginHorizontal: -24, marginTop: 7, marginBottom: 12 },
  pip: { position: "absolute", right: 24, top: 16, width: 120, height: 160 },
  board: { position: "absolute", left: 24, top: 12 },
  split: { paddingTop: 2, paddingHorizontal: 24, gap: 16 },
  splitCamera: { height: 198 },
  splitPanel: { height: 184, borderRadius: radius.r12, overflow: "hidden" },
  playback: { position: "absolute", left: 24, right: 24, bottom: 10, height: 48, borderRadius: radius.r12, backgroundColor: camera.surface, flexDirection: "row", alignItems: "center", gap: 10, paddingRight: 12 },
  playBtn: { width: 44, height: 44, alignItems: "center", justifyContent: "center", marginLeft: 2 },
  pbTrack: { height: 3, borderRadius: radius.r2, backgroundColor: camera.track, marginTop: 8, overflow: "hidden" },
  pbFill: { height: 3, backgroundColor: camera.accent },
});
