/**
 * `plotly.js-basic-dist-min` ships no type declarations of its own — it is a
 * pre-built UMD bundle. `@types/plotly.js` types the full library, so this maps the
 * basic bundle onto the subset of that API this project actually calls.
 *
 * Declaring only what is used is deliberate. Re-exporting the whole of plotly.js
 * would type functions the basic bundle does not contain, and a call to one would
 * typecheck cleanly and then fail at runtime.
 */
declare module "plotly.js-basic-dist-min" {
  import type { Config, Data, Layout } from "plotly.js";

  export function newPlot(
    root: HTMLElement,
    data: Data[],
    layout?: Partial<Layout>,
    config?: Partial<Config>,
  ): Promise<HTMLElement>;

  export function purge(root: HTMLElement): void;

  const Plotly: {
    newPlot: typeof newPlot;
    purge: typeof purge;
  };

  export default Plotly;
}
