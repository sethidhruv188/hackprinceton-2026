// The agent's body: a procedurally modelled robot (no third-party model; geometry and materials are built here).
// Brushed-metal shell, dark glass visor, emissive eye strips and chest core, studio lighting from three's
// RoomEnvironment (generated locally, no network), soft contact shadow. Motion is driven by `mood`.
import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

export const MOOD_COLOR = {
  idle: "#22d3ee", listening: "#38bdf8", thinking: "#a78bfa", speaking: "#2dd4bf", alert: "#fb923c", offline: "#000000",
};

// Pointer position in -1..1 across the whole window (the head follows it from any page position).
const pointer = { x: 0, y: 0 };
if (typeof window !== "undefined") {
  window.addEventListener("pointermove", (e) => {
    pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
    pointer.y = -((e.clientY / window.innerHeight) * 2 - 1);
  }, { passive: true });
}

function Studio() {
  const { gl, scene } = useThree();
  useEffect(() => {
    const pm = new THREE.PMREMGenerator(gl);
    const env = pm.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = env;
    return () => { env.dispose(); pm.dispose(); scene.environment = null; };
  }, [gl, scene]);
  return (
    <>
      <ambientLight intensity={0.25} />
      <directionalLight position={[2.5, 4, 3]} intensity={1.6} castShadow shadow-mapSize={[1024, 1024]} shadow-bias={-0.0004}
                        shadow-camera-left={-3} shadow-camera-right={3} shadow-camera-top={3} shadow-camera-bottom={-3} />
      <directionalLight position={[-3, 2, -2]} intensity={0.9} color="#67e8f9" />
      <directionalLight position={[3, 1, -3]} intensity={0.5} color="#fdba74" />
    </>
  );
}

function useMaterials() {
  return useMemo(() => {
    // Brushed metal: anisotropic highlights stretched along the surface.
    const metal = new THREE.MeshPhysicalMaterial({ color: "#c7cbd3", metalness: 1, roughness: 0.34, anisotropy: 0.7, clearcoat: 0.35, clearcoatRoughness: 0.4, envMapIntensity: 1.1 });
    const dark = new THREE.MeshPhysicalMaterial({ color: "#2a2e36", metalness: 0.9, roughness: 0.45, envMapIntensity: 0.9 });
    const joint = new THREE.MeshPhysicalMaterial({ color: "#16181d", metalness: 0.6, roughness: 0.6 });
    const glass = new THREE.MeshPhysicalMaterial({ color: "#05080f", metalness: 0.25, roughness: 0.04, clearcoat: 1, clearcoatRoughness: 0.02, envMapIntensity: 1.8, reflectivity: 1 });
    const glow = new THREE.MeshStandardMaterial({ color: "#000000", emissive: new THREE.Color("#22d3ee"), emissiveIntensity: 2.2, toneMapped: false });
    const glowSoft = new THREE.MeshStandardMaterial({ color: "#000000", emissive: new THREE.Color("#22d3ee"), emissiveIntensity: 1.2, toneMapped: false, transparent: true, opacity: 0.9 });
    return { metal, dark, joint, glass, glow, glowSoft };
  }, []);
}

function Robot({ mood, reduced, onClick }) {
  const m = useMaterials();
  const geo = useMemo(() => ({
    head: new RoundedBoxGeometry(1.25, 0.95, 1, 6, 0.24),
    visor: new RoundedBoxGeometry(1.06, 0.44, 0.12, 6, 0.1),
    eye: new RoundedBoxGeometry(0.26, 0.07, 0.02, 3, 0.03),
    torso: new RoundedBoxGeometry(1.5, 1.15, 0.95, 6, 0.28),
    plate: new RoundedBoxGeometry(0.9, 0.55, 0.06, 4, 0.08),
  }), []);
  const root = useRef(), head = useRef(), body = useRef(), scan = useRef(), core = useRef(), ring = useRef(), armL = useRef(), armR = useRef();
  const color = useMemo(() => new THREE.Color(), []);
  const look = useRef({ x: 0, y: 0, glanceAt: 2, glance: 0 });

  useFrame((state, dt) => {
    const t = state.clock.elapsedTime;
    const target = new THREE.Color(MOOD_COLOR[mood] || MOOD_COLOR.idle);
    color.lerp(target, Math.min(1, dt * 4));
    const off = mood === "offline";
    // light intensity per state
    let eye = off ? 0 : 2.2, coreI = off ? 0 : 1.4;
    if (mood === "thinking") coreI = 1 + Math.sin(t * 6) * 0.8;
    if (mood === "speaking") eye = 1.6 + Math.abs(Math.sin(t * 13)) * 1.6;
    if (mood === "alert") { eye = 1.6 + Math.sin(t * 3) * 0.8; coreI = 1.2 + Math.sin(t * 3) * 0.6; }
    if (mood === "listening") eye = 2.8;
    m.glow.emissive.copy(color); m.glow.emissiveIntensity = THREE.MathUtils.lerp(m.glow.emissiveIntensity, eye, Math.min(1, dt * 6));
    m.glowSoft.emissive.copy(color); m.glowSoft.emissiveIntensity = THREE.MathUtils.lerp(m.glowSoft.emissiveIntensity, coreI, Math.min(1, dt * 6));
    if (reduced) return;
    // breathing
    body.current.scale.y = 1 + Math.sin(t * 1.6) * (off ? 0 : 0.012);
    root.current.position.y = -0.55 + Math.sin(t * 1.6) * (off ? 0 : 0.015);
    // head: follow the pointer, glance around now and then, droop when offline
    const L = look.current;
    if (t > L.glanceAt) { L.glance = (Math.random() - 0.5) * 0.8; L.glanceAt = t + 3 + Math.random() * 4; }
    if (t > L.glanceAt - 2.2) L.glance *= 0.97;
    const tx = off ? 0 : pointer.x * 0.55 + (mood === "idle" ? L.glance : 0);
    const ty = off ? -0.35 : pointer.y * 0.3;
    L.x = THREE.MathUtils.lerp(L.x, tx, Math.min(1, dt * 3));
    L.y = THREE.MathUtils.lerp(L.y, ty, Math.min(1, dt * 3));
    head.current.rotation.y = L.x;
    head.current.rotation.x = -L.y + (mood === "listening" ? 0.12 : 0);
    // listening: lean in
    root.current.rotation.x = THREE.MathUtils.lerp(root.current.rotation.x, mood === "listening" ? 0.12 : off ? 0.06 : 0, Math.min(1, dt * 3));
    // thinking: visor scan line
    scan.current.visible = mood === "thinking";
    scan.current.position.x = Math.sin(t * 3.2) * 0.42;
    ring.current.rotation.z += dt * (mood === "thinking" ? 3 : 0.4);
    core.current.scale.setScalar(1 + (mood === "thinking" ? Math.sin(t * 6) * 0.08 : 0));
    // arms: slight sway; speaking adds a small gesture
    const g = mood === "speaking" ? Math.sin(t * 4) * 0.12 : 0;
    armL.current.rotation.z = 0.12 + Math.sin(t * 1.6) * 0.02 + g;
    armR.current.rotation.z = -0.12 - Math.sin(t * 1.6) * 0.02 - g;
  });

  return (
    <group ref={root} position={[0, -0.55, 0]} onClick={onClick}
           onPointerOver={() => { document.body.style.cursor = "pointer"; }} onPointerOut={() => { document.body.style.cursor = ""; }}>
      <group ref={body}>
        {/* torso */}
        <mesh geometry={geo.torso} material={m.metal} castShadow receiveShadow />
        <mesh geometry={geo.plate} material={m.dark} position={[0, 0.05, 0.48]} />
        <mesh ref={ring} position={[0, 0.05, 0.52]} material={m.glowSoft}><torusGeometry args={[0.17, 0.022, 12, 48]} /></mesh>
        <mesh ref={core} position={[0, 0.05, 0.52]} material={m.glow}><sphereGeometry args={[0.07, 24, 24]} /></mesh>
        {[-0.3, 0.3].map((x) => <mesh key={x} position={[x, -0.18, 0.505]} material={m.glowSoft}><boxGeometry args={[0.16, 0.025, 0.01]} /></mesh>)}
        {/* shoulders and arms */}
        {[[-1, armL], [1, armR]].map(([s, ref]) => (
          <group key={s} position={[s * 0.88, 0.32, 0]}>
            <mesh material={m.joint} castShadow><sphereGeometry args={[0.2, 32, 32]} /></mesh>
            <group ref={ref}>
              <mesh material={m.metal} position={[s * 0.06, -0.48, 0]} castShadow><capsuleGeometry args={[0.13, 0.62, 8, 24]} /></mesh>
              <mesh material={m.joint} position={[s * 0.06, -0.92, 0]}><sphereGeometry args={[0.12, 24, 24]} /></mesh>
            </group>
          </group>
        ))}
      </group>
      {/* neck */}
      <mesh material={m.joint} position={[0, 0.72, 0]}><cylinderGeometry args={[0.16, 0.2, 0.3, 32]} /></mesh>
      {[0.66, 0.76].map((y) => <mesh key={y} material={m.dark} position={[0, y, 0]}><torusGeometry args={[0.19, 0.018, 8, 40]} /></mesh>)}
      {/* head */}
      <group ref={head} position={[0, 1.32, 0]}>
        <mesh geometry={geo.head} material={m.metal} castShadow />
        <mesh geometry={geo.visor} material={m.glass} position={[0, 0.02, 0.47]} />
        {[-0.24, 0.24].map((x) => <mesh key={x} geometry={geo.eye} material={m.glow} position={[x, 0.03, 0.535]} />)}
        <mesh ref={scan} material={m.glow} position={[0, 0.02, 0.538]} visible={false}><boxGeometry args={[0.04, 0.34, 0.005]} /></mesh>
        {/* ear modules */}
        {[-1, 1].map((s) => (
          <group key={s} position={[s * 0.66, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
            <mesh material={m.dark}><cylinderGeometry args={[0.2, 0.2, 0.12, 32]} /></mesh>
            <mesh material={m.glowSoft} position={[0, s * 0.065, 0]}><torusGeometry args={[0.12, 0.014, 8, 40]} /></mesh>
          </group>
        ))}
        {/* sensor mast */}
        <mesh material={m.dark} position={[0.32, 0.6, -0.1]}><cylinderGeometry args={[0.025, 0.035, 0.28, 16]} /></mesh>
        <mesh material={m.glow} position={[0.32, 0.76, -0.1]}><sphereGeometry args={[0.04, 16, 16]} /></mesh>
      </group>
    </group>
  );
}

function Floor() {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -1.58, 0]} receiveShadow>
      <circleGeometry args={[2.6, 48]} />
      <shadowMaterial transparent opacity={0.18} />
    </mesh>
  );
}

// Renders only while visible on screen (IntersectionObserver) to keep the page at 60fps.
export default function Robot3D({ mood = "idle", onClick, camera = [0, 0.4, 5.2], fov = 34 }) {
  const wrap = useRef(null);
  const [visible, setVisible] = useState(true);
  const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  useEffect(() => {
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { threshold: 0.01 });
    if (wrap.current) io.observe(wrap.current);
    return () => io.disconnect();
  }, []);
  return (
    <div ref={wrap} style={{ width: "100%", height: "100%" }}>
      <Canvas shadows dpr={[1, 1.75]} frameloop={reduced ? "demand" : visible ? "always" : "never"}
              camera={{ position: camera, fov }} gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
              onCreated={({ gl }) => { gl.toneMapping = THREE.ACESFilmicToneMapping; gl.toneMappingExposure = 1.05; }}>
        <Studio />
        <Robot mood={mood} reduced={reduced} onClick={onClick} />
        <Floor />
      </Canvas>
    </div>
  );
}
