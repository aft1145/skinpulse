// 设计令牌（看板改版 spec §3.1，与视觉稿第 3 版一致）。颜色从不单独表达含义：
// 排名同时有数字、涨跌同时有正负号和箭头、状态同时有文字。
export type Mode = 'light' | 'dark'

export interface Tokens {
  bg: string
  glow: string
  headerBg: string
  surface: string
  surface2: string
  bar: string
  segBg: string
  tipBg: string
  line: string
  lineSoft: string
  lineStrong: string
  text: string
  text2: string
  text3: string
  accent: string
  accentHover: string
  accentPressed: string
  accentSoft: string
  accentSoftStrong: string
  onAccent: string
  gold: string
  goldSoft: string
  silver: string
  silverSoft: string
  bronze: string
  bronzeSoft: string
  up: string
  down: string
  ok: string
  okSoft: string
  warn: string
  warnSoft: string
  /** 会亏的差价（价位视图）。不用涨跌色：涨红跌绿的红表示涨，拿来表示亏会读反 */
  loss: string
  lossSoft: string
  idle: string
  idleSoft: string
  amber: string
  amberSoft: string
  /** 中性的「一般」档（求购表价格梯，2026-10-09）：和 MA30 同一个蓝，不和涨跌、警示色混 */
  info: string
  infoSoft: string
  stattrak: string
  /** K 线均线：MA7、MA30（和视觉稿第 3 版一致） */
  ma7: string
  ma30: string
  flashUp: string
  flashDown: string
  shadow: string
  popShadow: string
}

export const TOKENS: Record<Mode, Tokens> = {
  light: {
    bg: '#EEF0F3',
    glow: 'none',
    headerBg: '#FFFFFF',
    surface: '#FFFFFF',
    surface2: '#F6F8FA',
    bar: '#E4E7EC',
    segBg: '#E4E7EC',
    tipBg: '#FFFFFF',
    line: '#DCE0E6',
    lineSoft: '#EBEEF2',
    lineStrong: '#C5CBD4',
    text: '#0E1116',
    text2: '#3A414D',
    text3: '#586070',
    accent: '#0A6B69',
    accentHover: '#0C7D7A',
    accentPressed: '#085A58',
    accentSoft: '#E1F1F0',
    accentSoftStrong: '#9CD3D0',
    onAccent: '#FFFFFF',
    gold: '#8A5700',
    goldSoft: '#FBEFD4',
    silver: '#475569',
    silverSoft: '#EEF1F5',
    bronze: '#914E24',
    bronzeSoft: '#F8EBE1',
    up: '#C8313A',
    down: '#0E7A4F',
    ok: '#0C7A4E',
    okSoft: '#DDF1E7',
    warn: '#A1480C',
    warnSoft: '#FCEBDD',
    loss: '#B42318',
    lossSoft: '#FDECEA',
    idle: '#8A919C',
    idleSoft: '#EEF0F3',
    amber: '#8A5300',
    amberSoft: '#FDF1DB',
    info: '#3B5BDB',
    infoSoft: '#E7ECFD',
    stattrak: '#A94A0C',
    ma7: '#B7791F',
    ma30: '#3B5BDB',
    flashUp: '#FDF3DC',
    flashDown: '#FCE9E7',
    shadow: '0 1px 2px rgba(16,24,40,0.05)',
    popShadow: '0 12px 32px rgba(16,24,40,0.16)',
  },
  dark: {
    bg: '#0B0D11',
    glow: 'radial-gradient(1000px 420px at 20% -10%, rgba(61,219,217,0.08), transparent 65%)',
    headerBg: '#0B0D11',
    surface: '#13161D',
    surface2: '#171B23',
    bar: '#262C38',
    segBg: '#1A1E27',
    tipBg: '#1B1F28',
    line: '#232833',
    lineSoft: '#1C2029',
    lineStrong: '#343B48',
    text: '#EEF1F5',
    text2: '#B2B9C5',
    text3: '#8C94A3',
    accent: '#46D6D3',
    accentHover: '#62DEDB',
    accentPressed: '#35BDBA',
    accentSoft: 'rgba(70,214,211,0.12)',
    accentSoftStrong: 'rgba(70,214,211,0.45)',
    onAccent: '#05201F',
    gold: '#F5C451',
    goldSoft: 'rgba(245,196,81,0.14)',
    silver: '#C3CEDF',
    silverSoft: 'rgba(195,206,223,0.10)',
    bronze: '#E39A6B',
    bronzeSoft: 'rgba(227,154,107,0.12)',
    up: '#FF6B6B',
    down: '#3DD39B',
    ok: '#3DD39B',
    okSoft: 'rgba(61,211,155,0.16)',
    warn: '#FFA05C',
    warnSoft: 'rgba(255,160,92,0.13)',
    loss: '#FF7A7A',
    lossSoft: 'rgba(255,122,122,0.13)',
    idle: '#8C94A3',
    idleSoft: '#1C2029',
    amber: '#FFB547',
    amberSoft: 'rgba(255,181,71,0.13)',
    info: '#8FA8FF',
    infoSoft: 'rgba(143,168,255,0.14)',
    stattrak: '#FF8A3D',
    ma7: '#F5C451',
    ma30: '#8FA8FF',
    flashUp: 'rgba(245,196,81,0.12)',
    flashDown: 'rgba(255,107,107,0.10)',
    shadow: '0 1px 0 rgba(255,255,255,0.03) inset',
    popShadow: '0 16px 40px rgba(0,0,0,0.5)',
  },
}

/** 第 1/2/3 名的奖牌色（背景、文字、描边）；其余名次是中性描边。 */
export function medalColors(t: Tokens, rank: number | null): { bg: string; fg: string; line: string } {
  if (rank === 1) return { bg: t.goldSoft, fg: t.gold, line: t.gold }
  if (rank === 2) return { bg: t.silverSoft, fg: t.silver, line: t.silver }
  if (rank === 3) return { bg: t.bronzeSoft, fg: t.bronze, line: t.bronze }
  return { bg: 'transparent', fg: t.text2, line: t.lineStrong }
}
