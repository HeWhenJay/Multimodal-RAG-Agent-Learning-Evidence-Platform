import { Float, Line, OrbitControls, Sparkles } from '@react-three/drei';
import { Canvas, useFrame } from '@react-three/fiber';
import { useMemo, useRef } from 'react';
import type { Group, Mesh } from 'three';

interface EvidenceNode {
  label: string;
  color: string;
  position: [number, number, number];
}

const evidenceNodes: EvidenceNode[] = [
  { label: 'PDF', color: '#4f46e5', position: [-2.25, 0.72, 0.2] },
  { label: 'OCR', color: '#0891b2', position: [-0.9, 1.65, -0.35] },
  { label: 'BM25', color: '#15803d', position: [1.05, 1.28, 0.4] },
  { label: 'RRF', color: '#a54100', position: [2.28, -0.22, -0.15] },
  { label: 'JD', color: '#7c3aed', position: [0.42, -1.55, 0.48] },
  { label: 'CITE', color: '#0ea5e9', position: [-1.55, -1.1, -0.42] }
];

export function LoginRagScene() {
  const reducedMotion = useMemo(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    []
  );

  return (
    <Canvas
      camera={{ position: [0, 0.2, 6.2], fov: 45 }}
      dpr={[1, 1.6]}
      gl={{ antialias: true, alpha: true }}
    >
      <ambientLight intensity={0.7} />
      <pointLight position={[3.5, 4, 3]} intensity={35} color="#dbeafe" />
      <pointLight position={[-4, -1, 2]} intensity={18} color="#fed7aa" />
      <RagConstellation reducedMotion={reducedMotion} />
      <Sparkles count={42} scale={[6.6, 3.8, 2.8]} size={2.5} speed={0.35} color="#cbd5e1" />
      <OrbitControls
        autoRotate={!reducedMotion}
        autoRotateSpeed={0.55}
        enablePan={false}
        enableZoom={false}
        minPolarAngle={Math.PI / 2.9}
        maxPolarAngle={Math.PI / 1.9}
      />
    </Canvas>
  );
}

function RagConstellation({ reducedMotion }: { reducedMotion: boolean }) {
  const groupRef = useRef<Group>(null);
  const coreRef = useRef<Mesh>(null);

  useFrame((state, delta) => {
    if (reducedMotion) {
      return;
    }

    if (groupRef.current) {
      groupRef.current.rotation.y += delta * 0.16;
      groupRef.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.28) * 0.08;
    }

    if (coreRef.current) {
      const scale = 1 + Math.sin(state.clock.elapsedTime * 1.7) * 0.04;
      coreRef.current.scale.setScalar(scale);
    }
  });

  return (
    <group ref={groupRef}>
      <mesh ref={coreRef}>
        <icosahedronGeometry args={[0.78, 2]} />
        <meshPhysicalMaterial
          color="#eef2ff"
          roughness={0.28}
          metalness={0.15}
          transmission={0.2}
          thickness={0.9}
          emissive="#1d4ed8"
          emissiveIntensity={0.18}
        />
      </mesh>

      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[1.55, 0.012, 16, 128]} />
        <meshBasicMaterial color="#38bdf8" transparent opacity={0.55} />
      </mesh>
      <mesh rotation={[0.9, 0.32, 0.1]}>
        <torusGeometry args={[2.25, 0.01, 16, 128]} />
        <meshBasicMaterial color="#f59e0b" transparent opacity={0.36} />
      </mesh>
      <mesh rotation={[0.2, 1.1, 0.7]}>
        <torusGeometry args={[2.95, 0.008, 16, 128]} />
        <meshBasicMaterial color="#22c55e" transparent opacity={0.28} />
      </mesh>

      {evidenceNodes.map((node) => (
        <group key={node.label}>
          <Line
            points={[[0, 0, 0], node.position]}
            color={node.color}
            lineWidth={1}
            transparent
            opacity={0.45}
          />
          <EvidenceNode node={node} />
        </group>
      ))}
    </group>
  );
}

function EvidenceNode({ node }: { node: EvidenceNode }) {
  const meshRef = useRef<Mesh>(null);
  const phase = node.position[0] + node.position[1];

  useFrame((state) => {
    if (meshRef.current) {
      const scale = 1 + Math.sin(state.clock.elapsedTime * 2.2 + phase) * 0.08;
      meshRef.current.scale.setScalar(scale);
    }
  });

  return (
    <Float speed={1.25} rotationIntensity={0.28} floatIntensity={0.22}>
      <mesh ref={meshRef} position={node.position}>
        <sphereGeometry args={[0.2, 32, 32]} />
        <meshStandardMaterial
          color={node.color}
          emissive={node.color}
          emissiveIntensity={0.38}
          roughness={0.32}
          metalness={0.2}
        />
      </mesh>
    </Float>
  );
}
