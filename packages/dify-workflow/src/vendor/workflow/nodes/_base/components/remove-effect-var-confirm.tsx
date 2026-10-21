export default function RemoveEffectVarConfirm({
  isShow,
  onCancel,
  onConfirm,
}: {
  isShow: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!isShow) return null;
  return (
    <div className="dify-modal-backdrop">
      <div role="alertdialog" aria-label="删除变量" className="dify-modal">
        <p>该变量已被引用，请先修改引用。</p>
        <button type="button" onClick={onCancel}>
          关闭
        </button>
        <button type="button" disabled onClick={onConfirm}>
          删除
        </button>
      </div>
    </div>
  );
}
