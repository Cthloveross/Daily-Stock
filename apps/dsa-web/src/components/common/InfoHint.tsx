import { Tooltip } from './Tooltip';

/**
 * 界面披露策略（2026-08-15，用户反馈「不要急着撇清关系」）：诚实边界与
 * 口径原文**一字不删**，但不再以整句样板话占据可见界面——统一收进这个
 * 小 ⓘ 的 tooltip（每个面板最多一枚，挂在标题/角落）。可见界面只保留
 * as-of 时点、标缺原因与逐读数的口径标注（它们是数据标签，不是免责声明）。
 *
 * aria-label 携带完整原文：读屏与悬停读到同一份内容，隐藏 ≠ 删除。
 */
export function InfoHint({
  text,
  label = '口径与诚实边界',
}: {
  /** 诚实边界原文（逐字），多行用 \n 分隔。 */
  text: string;
  /** aria 前缀，说明这枚 ⓘ 属于哪块内容。 */
  label?: string;
}) {
  return (
    <Tooltip focusable contentClassName="whitespace-pre-line" content={text}>
      <span
        aria-label={`${label}：${text}`}
        className="cursor-help text-caption text-text-3"
      >
        ⓘ
      </span>
    </Tooltip>
  );
}

export default InfoHint;
