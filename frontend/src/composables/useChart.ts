import {
  Chart,
  type ChartConfiguration,
  type ChartType,
  type DefaultDataPoint,
} from "chart.js";
import { onUnmounted, ref, type Ref } from "vue";

/**
 * Manages a Chart.js instance lifecycle on a <canvas> element.
 * The consumer calls create() after data is loaded and the canvas
 * is in the DOM (typically after nextTick). Destroyed on unmount.
 */
export function useChart<T extends ChartType>(
  canvasRef: Ref<HTMLCanvasElement | undefined>,
  getConfig: () => ChartConfiguration<T, DefaultDataPoint<T>>,
) {
  let chart: Chart<T, DefaultDataPoint<T>> | null = null;

  function create() {
    destroy();
    if (!canvasRef.value) {
      console.warn("[useChart] create called but canvas ref is undefined");
      return;
    }
    chart = new Chart<T, DefaultDataPoint<T>>(canvasRef.value, getConfig());
  }

  function destroy() {
    if (chart) {
      chart.destroy();
      chart = null;
    }
  }

  function update() {
    if (!chart) return;
    const next = getConfig();
    chart.data = next.data;
    chart.options = next.options;
    chart.update();
  }

  onUnmounted(destroy);

  return { chart: ref(chart), create, destroy, update };
}
