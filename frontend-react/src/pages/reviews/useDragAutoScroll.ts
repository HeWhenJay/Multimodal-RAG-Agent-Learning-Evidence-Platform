import { useCallback, useEffect, useRef } from 'react';

const EDGE_SCROLL_ZONE_PX = 128;
const MAX_SCROLL_STEP_PX = 22;

// 拖拽接近视口上下边缘时连续滚动页面，并在离开边缘或结束拖拽后立即停止。
export function useDragAutoScroll() {
  const scrollStepRef = useRef(0);
  const animationFrameRef = useRef<number | null>(null);

  const runScrollFrame = useCallback(() => {
    const step = scrollStepRef.current;
    if (!step) {
      animationFrameRef.current = null;
      return;
    }
    window.scrollBy(0, step);
    animationFrameRef.current = window.requestAnimationFrame(runScrollFrame);
  }, []);

  const stopDragAutoScroll = useCallback(() => {
    scrollStepRef.current = 0;
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
  }, []);

  // 根据指针到视口边缘的距离计算滚动速度，越靠近边缘速度越快。
  const updateDragAutoScroll = useCallback((clientY: number) => {
    const viewportHeight = window.innerHeight;
    const edgeZone = Math.min(EDGE_SCROLL_ZONE_PX, Math.max(72, viewportHeight * 0.18));
    let intensity = 0;
    if (clientY < edgeZone) intensity = -Math.min(1, (edgeZone - clientY) / edgeZone);
    if (clientY > viewportHeight - edgeZone) intensity = Math.min(1, (clientY - (viewportHeight - edgeZone)) / edgeZone);
    if (!intensity) {
      stopDragAutoScroll();
      return;
    }
    scrollStepRef.current = Math.sign(intensity) * Math.max(2, Math.ceil(MAX_SCROLL_STEP_PX * intensity * intensity));
    if (animationFrameRef.current === null) animationFrameRef.current = window.requestAnimationFrame(runScrollFrame);
  }, [runScrollFrame, stopDragAutoScroll]);

  useEffect(() => stopDragAutoScroll, [stopDragAutoScroll]);

  return { updateDragAutoScroll, stopDragAutoScroll };
}
