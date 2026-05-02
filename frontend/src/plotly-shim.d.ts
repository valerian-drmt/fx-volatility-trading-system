// plotly.js-basic-dist-min ships no types. Fall back to the public @types/plotly.js
// signature for the factory call — react-plotly.js only needs the module object.
declare module "plotly.js-basic-dist-min" {
  import type * as Plotly from "plotly.js";
  const plotly: typeof Plotly;
  export default plotly;
}
