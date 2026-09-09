"use client";

/**
 * A live 3D model of the property. Nothing here is decorative:
 *   - every window is a real room, lit when that room is sold today
 *   - every floating marker is a real asset, coloured and pulsed by its risk score
 * so the scene is another view onto the same data spine the engines read.
 */

import { useLayoutEffect, useMemo, useRef, useState, useEffect } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { AssetHealth } from "@/lib/api";

const COLS = 12;
const FLOOR_H = 0.62;
const BUILDING_W = 6.4;
const BUILDING_D = 3.0;

const STATUS_COLOR: Record<string, string> = {
  critical: "#ff4d4d",
  watch: "#ffb020",
  healthy: "#22c55e",
};

/** Deterministic PRNG - the lit-room pattern must not reshuffle on every render. */
function mulberry32(seed: number) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Layout = { total: number; rows: number; height: number };

function useLayout(total: number): Layout {
  return useMemo(() => {
    const safe = Math.max(total, 1);
    const rows = Math.max(1, Math.ceil(safe / (COLS * 2)));
    return { total: safe, rows, height: rows * FLOOR_H };
  }, [total]);
}

/** The room grid. One instance per room, coloured lit or dark. */
function Windows({ total, sold, layout }: { total: number; sold: number; layout: Layout }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const { height } = layout;

  const litSet = useMemo(() => {
    // stable pseudo-random spread of sold rooms across the facade
    const idx = Array.from({ length: total }, (_, i) => i);
    const rand = mulberry32(1337);
    for (let i = idx.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      const tmp = idx[i];
      idx[i] = idx[j];
      idx[j] = tmp;
    }
    return new Set(idx.slice(0, Math.max(0, Math.min(sold, total))));
  }, [total, sold]);

  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const m = new THREE.Matrix4();
    const lit = new THREE.Color("#ffd9a0");
    const dark = new THREE.Color("#243040");

    for (let i = 0; i < total; i++) {
      const side = i % 2 === 0 ? 1 : -1;
      const slot = Math.floor(i / 2);
      const row = Math.floor(slot / COLS);
      const col = slot % COLS;

      const x = -BUILDING_W / 2 + 0.42 + (col * (BUILDING_W - 0.84)) / Math.max(COLS - 1, 1);
      const y = -height / 2 + FLOOR_H * 0.55 + row * FLOOR_H;
      const z = side * (BUILDING_D / 2 + 0.015);

      m.makeTranslation(x, y, z);
      mesh.setMatrixAt(i, m);
      mesh.setColorAt(i, litSet.has(i) ? lit : dark);
    }
    mesh.count = total;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [total, litSet, height]);

  return (
    <instancedMesh ref={ref} args={[undefined, undefined, Math.max(total, 1)]}>
      <boxGeometry args={[0.3, 0.26, 0.02]} />
      {/* basic material: unlit windows stay flat dark, lit ones read as emissive */}
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  );
}

function Building({ layout, sold }: { layout: Layout; sold: number }) {
  const { total, height } = layout;
  return (
    <group position={[0, height / 2 + 0.12, 0]}>
      {/* main mass */}
      <mesh castShadow receiveShadow>
        <boxGeometry args={[BUILDING_W, height, BUILDING_D]} />
        <meshStandardMaterial color="#141c28" roughness={0.75} metalness={0.15} />
      </mesh>
      {/* roof slab */}
      <mesh position={[0, height / 2 + 0.06, 0]} castShadow>
        <boxGeometry args={[BUILDING_W + 0.35, 0.12, BUILDING_D + 0.35]} />
        <meshStandardMaterial color="#0d1420" roughness={0.6} metalness={0.3} />
      </mesh>
      {/* rooftop crown light */}
      <mesh position={[0, height / 2 + 0.2, 0]}>
        <boxGeometry args={[BUILDING_W - 1.2, 0.03, 0.06]} />
        <meshBasicMaterial color="#3987e5" toneMapped={false} />
      </mesh>
      <Windows total={total} sold={sold} layout={layout} />
    </group>
  );
}

/** Podium, pool and ground - so the tower reads as a property, not a floating box. */
function Grounds() {
  return (
    <group>
      <mesh position={[0, 0.06, 0]} receiveShadow>
        <boxGeometry args={[BUILDING_W + 2.6, 0.12, BUILDING_D + 2.6]} />
        <meshStandardMaterial color="#0b111a" roughness={0.9} />
      </mesh>
      <mesh position={[0, 0.13, BUILDING_D / 2 + 1.35]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[3.4, 1.15]} />
        <meshStandardMaterial
          color="#0a4d5f"
          emissive="#0e7490"
          emissiveIntensity={0.28}
          roughness={0.15}
          metalness={0.7}
        />
      </mesh>
      <mesh position={[0, -0.001, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <circleGeometry args={[13, 64]} />
        <meshStandardMaterial color="#070a10" roughness={1} />
      </mesh>
    </group>
  );
}

/** One asset, floating at an elevation set by its risk. Critical assets pulse. */
function AssetMarker({
  asset,
  position,
  onHover,
}: {
  asset: AssetHealth;
  position: [number, number, number];
  onHover: (a: AssetHealth | null) => void;
}) {
  const ref = useRef<THREE.Group>(null);
  const color = STATUS_COLOR[asset.status] ?? "#94a3b8";
  const critical = asset.status === "critical";

  useFrame((state) => {
    const g = ref.current;
    if (!g) return;
    const t = state.clock.elapsedTime;
    g.position.y = position[1] + Math.sin(t * 1.2 + position[0]) * 0.07;
    g.rotation.y = t * 0.6;
    g.scale.setScalar(critical ? 1 + Math.sin(t * 4) * 0.12 : 1);
  });

  return (
    <group
      ref={ref}
      position={position}
      onPointerOver={(e) => {
        e.stopPropagation();
        onHover(asset);
      }}
      onPointerOut={() => onHover(null)}
    >
      <mesh>
        <octahedronGeometry args={[0.17, 0]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
      <mesh>
        <octahedronGeometry args={[0.28, 0]} />
        <meshBasicMaterial color={color} wireframe transparent opacity={0.45} toneMapped={false} />
      </mesh>
      {/* tether, so the marker reads as attached to the property */}
      <mesh position={[0, -position[1] / 2, 0]}>
        <cylinderGeometry args={[0.005, 0.005, position[1], 6]} />
        <meshBasicMaterial color={color} transparent opacity={0.25} toneMapped={false} />
      </mesh>
    </group>
  );
}

function AssetMarkers({
  assets,
  onHover,
}: {
  assets: AssetHealth[];
  onHover: (a: AssetHealth | null) => void;
}) {
  const placed = useMemo(() => {
    const n = Math.max(assets.length, 1);
    return assets.map((a, i) => {
      const angle = -Math.PI / 3 + (i / Math.max(n - 1, 1)) * ((2 * Math.PI) / 3);
      const radius = 4.1;
      // higher risk floats higher - risk becomes literal elevation
      const y = 0.85 + a.risk * 2.0;
      return {
        asset: a,
        position: [Math.sin(angle) * radius, y, Math.cos(angle) * radius] as [
          number,
          number,
          number,
        ],
      };
    });
  }, [assets]);

  return (
    <>
      {placed.map((p) => (
        <AssetMarker key={p.asset.id} asset={p.asset} position={p.position} onHover={onHover} />
      ))}
    </>
  );
}

/** Slow drifting motes - depth cue only. */
function Motes() {
  const ref = useRef<THREE.Points>(null);
  const geo = useMemo(() => {
    const n = 140;
    const pos = new Float32Array(n * 3);
    const rand = mulberry32(7);
    for (let i = 0; i < n; i++) {
      pos[i * 3] = (rand() - 0.5) * 18;
      pos[i * 3 + 1] = rand() * 7;
      pos[i * 3 + 2] = (rand() - 0.5) * 18;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return g;
  }, []);

  useFrame((state) => {
    if (ref.current) ref.current.rotation.y = state.clock.elapsedTime * 0.02;
  });

  return (
    <points ref={ref} geometry={geo}>
      <pointsMaterial size={0.04} color="#5b7fa8" transparent opacity={0.5} sizeAttenuation />
    </points>
  );
}

export type ResortSceneProps = {
  roomsSold: number;
  roomsAvailable: number;
  assets: AssetHealth[];
};

export default function ResortScene({ roomsSold, roomsAvailable, assets }: ResortSceneProps) {
  const layout = useLayout(roomsAvailable);
  const [hover, setHover] = useState<AssetHealth | null>(null);
  const [failed, setFailed] = useState(false);

  // WebGL is not guaranteed on every demo machine - degrade rather than crash.
  useEffect(() => {
    try {
      const c = document.createElement("canvas");
      if (!(c.getContext("webgl2") || c.getContext("webgl"))) setFailed(true);
    } catch {
      setFailed(true);
    }
  }, []);

  if (failed) {
    return (
      <div
        className="flex h-full w-full items-center justify-center text-center text-[13px]"
        style={{ color: "var(--text-muted)" }}
      >
        3D view unavailable on this device — the figures alongside are unaffected.
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <Canvas
        shadows
        dpr={[1, 2]}
        camera={{ position: [9.2, 5.2, 10.4], fov: 34 }}
        gl={{ antialias: true, alpha: true }}
      >
        <fog attach="fog" args={["#070a10", 14, 30]} />
        <ambientLight intensity={0.45} />
        <directionalLight
          position={[6, 9, 5]}
          intensity={1.15}
          castShadow
          shadow-mapSize={[1024, 1024]}
        />
        <pointLight position={[-6, 3, -4]} intensity={35} color="#3987e5" distance={18} />
        <pointLight position={[5, 2, 5]} intensity={22} color="#eb6834" distance={16} />

        <Grounds />
        <Building layout={layout} sold={roomsSold} />
        <AssetMarkers assets={assets} onHover={setHover} />
        <Motes />

        <OrbitControls
          autoRotate
          autoRotateSpeed={0.55}
          enablePan={false}
          enableZoom={false}
          target={[0, 1.45, 0]}
          minPolarAngle={Math.PI / 6}
          maxPolarAngle={Math.PI / 2.3}
        />
      </Canvas>

      {/* hover readout - plain DOM, so it stays crisp and themable */}
      {hover && (
        <div
          className="pointer-events-none absolute left-4 top-4 rounded-lg px-3 py-2 text-[12px]"
          style={{
            background: "rgba(7, 10, 16, 0.88)",
            border: `1px solid ${STATUS_COLOR[hover.status] ?? "#475569"}`,
            color: "#e6edf6",
            backdropFilter: "blur(8px)",
          }}
        >
          <div className="font-semibold">{hover.name}</div>
          <div style={{ color: "#9fb2c8" }}>
            {hover.location} · {Math.round(hover.risk * 100)}% risk
            {hover.days_to_failure > 0 ? ` · ${hover.days_to_failure}d to failure` : ""}
          </div>
        </div>
      )}
    </div>
  );
}
