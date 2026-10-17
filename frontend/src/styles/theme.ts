/* =====================================================================
 * 由 scripts/build_viz_theme.py 自动生成 —— 禁止手工编辑。
 * 唯一来源：configs/viz_theme.yaml（version v1.2，sha256 21cdf449571b6d80…）
 * 命名转写规则（_ → -）：yaml 的 snake_case 键 → CSS 变量名时下划线转连字符；
 *   TS/JS 键名一律以 yaml 为准（原样 snake_case，不转写）。
 * 重新生成：python scripts/build_viz_theme.py
 */


export const VIZ_THEME_META = {
  source: "configs/viz_theme.yaml",
  version: "v1.2",
  name: "zjtech-viz-theme",
  sha256: "21cdf449571b6d80a3850814ee814a34007799b698869c02197df46691ea5845",
} as const;

export const BRAND = {
  primary: "#4EA1FF",
  primary_dark: "#2F7FE0",
  bg_dark: "#0F1420",
  panel: "#182032",
  panel_2: "#1F293E",
  line: "#2A3550",
  text: "#E6ECF5",
  text_muted: "#8B9BB4",
} as const;

export type RiskBand = {
  band: number; code: string; label: string; reg_class: string;
  color: string; color_bg: string;
};

export const RISK_BANDS: readonly RiskBand[] = [
  {
    band: 1, code: "excellent", label: "优良",
    reg_class: "正常类", color: "#1E8E3E",
    color_bg: "#0E3B24",
  },
  {
    band: 2, code: "good", label: "较好",
    reg_class: "关注类", color: "#7BC96F",
    color_bg: "#1C3B22",
  },
  {
    band: 3, code: "watch", label: "关注",
    reg_class: "次级类", color: "#F2C94C",
    color_bg: "#3B3410",
  },
  {
    band: 4, code: "warning", label: "预警",
    reg_class: "可疑类", color: "#F2994A",
    color_bg: "#3B2410",
  },
  {
    band: 5, code: "high_risk", label: "高风险",
    reg_class: "损失类", color: "#E74C3C",
    color_bg: "#3B1C1C",
  },
] as const;

export const RISK_COLOR_BY_CODE: Record<string, string> = {
  excellent: "#1E8E3E",
  good: "#7BC96F",
  watch: "#F2C94C",
  warning: "#F2994A",
  high_risk: "#E74C3C",
};

export const RISK_COLOR_BY_BAND: Record<number, string> = {
  1: "#1E8E3E",
  2: "#7BC96F",
  3: "#F2C94C",
  4: "#F2994A",
  5: "#E74C3C",
};

export const ENTITY_COLORS = {
  company: "#4EA1FF",
  patent: "#9B59B6",
  investor: "#F1C40F",
  person: "#1ABC9C",
  field: "#E67E22",
  product: "#7F8C8D",
} as const;

// 涨跌色：A 股惯例 涨红跌绿（与 risk_band 属不同语义域）
export const MARKET_COLORS = {
  up: "#E74C3C",
  down: "#1E8E3E",
  flat: "#8B9BB4",
} as const;

export const COMPLIANCE = {
  disclaimer: "当前版本 PD 未经校准，真实面板区分度弱（行级 AUC≈0.59）。噪声地板 （置换对照 AUC，行级，来源 outputs/baseline_results.json.cv_row_shuffled_label） ＝ LR 0.492 / XGB 0.477，契约 meta.noise_floor 统一取 0.477。 仅用于原型系统演示与教学研究，不得用于真实授信决策。",
} as const;
