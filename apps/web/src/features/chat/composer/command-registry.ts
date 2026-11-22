import type { ComposerCommand, ComposerGroup, ComposerOption } from './types';

function matches(query: string, command: ComposerCommand) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  const label = command.label.replace(/^\//, '').toLowerCase();
  return (
    label.includes(needle) || command.description.toLowerCase().includes(needle)
  );
}

/** 把命令定义变成根菜单选项;不可用项保留,由菜单展示禁用原因。 */
export function filterCommandGroups(
  commands: ComposerCommand[],
  query: string,
): ComposerGroup[] {
  const needle = query.trim().toLowerCase();
  const options: ComposerOption[] = commands
    .filter((command) => matches(query, command))
    .sort((left, right) => {
      const leftPrefix = left.label
        .replace(/^\//, '')
        .toLowerCase()
        .startsWith(needle)
        ? 0
        : 1;
      const rightPrefix = right.label
        .replace(/^\//, '')
        .toLowerCase()
        .startsWith(needle)
        ? 0
        : 1;
      return leftPrefix - rightPrefix;
    })
    .map((command) => ({
      key: `cmd:${command.id}`,
      label: command.label,
      description: command.disabledReason ?? command.description,
      icon: command.icon,
      disabled: command.disabled,
      disabledReason: command.disabledReason,
      commandId: command.id,
    }));
  return options.length ? [{ key: 'commands', label: '命令', options }] : [];
}
