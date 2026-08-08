import { useEffect, useState, type KeyboardEvent } from 'react';

interface ReviewOrderPositionInputProps {
  currentIndex: number;
  itemCount: number;
  itemLabel: string;
  disabled?: boolean;
  onMove: (targetIndex: number) => void;
}

// 提供紧凑的数字排序入口，输入目标位置后按回车或移开焦点即可完成移动。
export function ReviewOrderPositionInput({ currentIndex, itemCount, itemLabel, disabled = false, onMove }: ReviewOrderPositionInputProps) {
  const currentPosition = currentIndex + 1;
  const [draft, setDraft] = useState(String(currentPosition));
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(String(currentPosition));
  }, [currentPosition, editing]);

  // 无效输入恢复当前位置，超出范围的数字自动限制到第一位或最后一位。
  function commitPosition(rawValue: string) {
    setEditing(false);
    const parsed = Number(rawValue);
    if (!Number.isInteger(parsed) || itemCount < 1) {
      setDraft(String(currentPosition));
      return;
    }
    const targetPosition = Math.max(1, Math.min(itemCount, parsed));
    setDraft(String(targetPosition));
    if (targetPosition !== currentPosition) onMove(targetPosition - 1);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    event.currentTarget.blur();
  }

  return (
    <label className="review-order-position-control" title={`输入 1 至 ${itemCount}，直接调整资料位置`}>
      <span aria-hidden="true">第</span>
      <input
        type="number"
        min={1}
        max={Math.max(1, itemCount)}
        step={1}
        inputMode="numeric"
        value={draft}
        disabled={disabled || itemCount < 2}
        aria-label={`将 ${itemLabel} 调整到第几位，共 ${itemCount} 位`}
        onFocus={(event) => {
          setEditing(true);
          event.currentTarget.select();
        }}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={(event) => commitPosition(event.currentTarget.value)}
        onKeyDown={handleKeyDown}
      />
      <span aria-hidden="true">位</span>
    </label>
  );
}
