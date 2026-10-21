/** 来源Dify1.17.1 nodes/variable-assigner/node.tsx；原聚合语义与宿主适配见SOURCE_MANIFEST。 */
import type { FC } from "react";
import type { NodeProps } from "../../../../types";
import type { VariableAssignerNodeType } from "./types";
import { memo, useMemo, useRef } from "react";
import { useTranslation } from "../../../../adapters/i18n";
import NodeGroupItem from "./components/node-group-item";

const i18nPrefix = "nodes.variableAssigner";

const Node: FC<NodeProps<VariableAssignerNodeType>> = (props) => {
  const { t } = useTranslation(["workflow"]);
  const ref = useRef<HTMLDivElement>(null);
  const { id, data } = props;
  const { advanced_settings } = data;

  const groups = useMemo(() => {
    if (!advanced_settings?.group_enabled) {
      return [
        {
          groupEnabled: false,
          targetHandleId: "target",
          title: t(($) => $[`${i18nPrefix}.title`], { ns: "workflow" }),
          type: data.output_type,
          variables: data.variables,
          variableAssignerNodeId: id,
          variableAssignerNodeData: data,
        },
      ];
    }
    return advanced_settings.groups.map((group) => {
      return {
        groupEnabled: true,
        targetHandleId: group.groupId,
        title: group.group_name,
        type: group.output_type,
        variables: group.variables,
        variableAssignerNodeId: id,
        variableAssignerNodeData: data,
      };
    });
  }, [t, advanced_settings, data, id]);

  return (
    <div className="relative mb-1 space-y-0.5 px-1" ref={ref}>
      {groups.map((item) => {
        return <NodeGroupItem key={item.title} item={item} />;
      })}
    </div>
  );
};

export default memo(Node);
