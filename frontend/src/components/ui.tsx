import {
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type Ref,
  type TextareaHTMLAttributes,
} from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as SelectPrimitive from "@radix-ui/react-select";
import * as MenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, ChevronRight, MoreHorizontal, Search, X } from "lucide-react";

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
}) {
  return <button type="button" className={`button button-${variant} ${className}`} {...props} />;
}
export function Input({
  label,
  className = "",
  ref,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label?: string; ref?: Ref<HTMLInputElement> }) {
  const id = useId();
  return (
    <label className={`field ${className}`} htmlFor={id}>
      {label && <span>{label}</span>}
      <input ref={ref} id={id} {...props} />
    </label>
  );
}
export function Textarea({
  label,
  className = "",
  ref,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label?: string;
  ref?: Ref<HTMLTextAreaElement>;
}) {
  const id = useId();
  return (
    <label className={`field ${className}`} htmlFor={id}>
      {label && <span>{label}</span>}
      <textarea ref={ref} id={id} {...props} />
    </label>
  );
}
export function Select({
  label,
  value,
  onChange,
  options,
  disabled = false,
  compact = false,
  icon,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly { value: string; label: string }[];
  disabled?: boolean;
  compact?: boolean;
  icon?: ReactNode;
}) {
  const id = useId();
  return (
    <div className={`field ${compact ? "select-compact" : ""}`}>
      {!compact && <label htmlFor={id}>{label}</label>}
      <SelectPrimitive.Root value={value} onValueChange={onChange} disabled={disabled}>
        <SelectPrimitive.Trigger id={id} className="select-trigger" aria-label={label}>
          {icon && <span className="select-trigger-icon">{icon}</span>}
          <SelectPrimitive.Value placeholder="请选择" />
          <ChevronDown size={14} />
        </SelectPrimitive.Trigger>
        <SelectPrimitive.Portal>
          <SelectPrimitive.Content className="select-content" position="popper" sideOffset={6}>
            <SelectPrimitive.Viewport>
              {options.map((option) => (
                <SelectPrimitive.Item
                  key={option.value}
                  value={option.value}
                  className="select-item"
                >
                  <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
                  <SelectPrimitive.ItemIndicator>
                    <Check size={14} />
                  </SelectPrimitive.ItemIndicator>
                </SelectPrimitive.Item>
              ))}
            </SelectPrimitive.Viewport>
          </SelectPrimitive.Content>
        </SelectPrimitive.Portal>
      </SelectPrimitive.Root>
    </div>
  );
}
export function Dialog({
  title,
  description,
  open,
  onOpenChange,
  children,
  wide = false,
  hideHeader = false,
  className = "",
}: {
  title: string;
  description?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  wide?: boolean;
  hideHeader?: boolean;
  className?: string;
}) {
  const descriptionId = useId();
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="dialog-overlay" />
        <DialogPrimitive.Content
          className={`dialog ${wide ? "dialog-wide" : ""} ${className}`}
          aria-describedby={description ? descriptionId : undefined}
        >
          {hideHeader ? (
            <DialogPrimitive.Title className="sr-only">{title}</DialogPrimitive.Title>
          ) : (
            <div className="dialog-heading">
              <DialogPrimitive.Title>{title}</DialogPrimitive.Title>
              <DialogPrimitive.Close asChild>
                <Button variant="ghost" aria-label="关闭">
                  <X size={18} />
                </Button>
              </DialogPrimitive.Close>
            </div>
          )}
          {description && (
            <DialogPrimitive.Description id={descriptionId} className="muted">
              {description}
            </DialogPrimitive.Description>
          )}
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
export function Switch({
  checked,
  onCheckedChange,
  disabled = false,
  label,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`switch-control ${checked ? "is-checked" : ""}`}
      onClick={() => onCheckedChange(!checked)}
    >
      <span className="switch-thumb" aria-hidden="true" />
    </button>
  );
}
export function Menu({
  label,
  children,
  iconOnly = false,
  icon,
  align = "end",
  triggerClassName = "",
}: {
  label: string;
  children: ReactNode;
  iconOnly?: boolean;
  icon?: ReactNode;
  align?: "start" | "center" | "end";
  triggerClassName?: string;
}) {
  return (
    <MenuPrimitive.Root>
      <MenuPrimitive.Trigger asChild>
        <Button
          variant="ghost"
          className={`menu-trigger ${iconOnly ? "icon-button" : ""} ${triggerClassName}`}
          aria-label={label}
          title={iconOnly ? label : undefined}
        >
          {iconOnly ? (
            (icon ?? <MoreHorizontal size={17} />)
          ) : (
            <>
              <span>{label}</span>
              <ChevronDown size={14} />
            </>
          )}
        </Button>
      </MenuPrimitive.Trigger>
      <MenuPrimitive.Portal>
        <MenuPrimitive.Content
          className="menu-content"
          align={align}
          sideOffset={6}
          collisionPadding={8}
        >
          {children}
        </MenuPrimitive.Content>
      </MenuPrimitive.Portal>
    </MenuPrimitive.Root>
  );
}
export function MenuSeparator() {
  return <MenuPrimitive.Separator className="menu-separator" />;
}
export function MenuSub({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <MenuPrimitive.Sub>
      <MenuPrimitive.SubTrigger className="menu-item menu-sub-trigger">
        <span className="menu-sub-trigger-label">
          {icon}
          <span>{label}</span>
        </span>
        <ChevronRight size={14} />
      </MenuPrimitive.SubTrigger>
      <MenuPrimitive.Portal>
        <MenuPrimitive.SubContent className="menu-content" sideOffset={4} collisionPadding={8}>
          {children}
        </MenuPrimitive.SubContent>
      </MenuPrimitive.Portal>
    </MenuPrimitive.Sub>
  );
}
export function MenuItem({
  children,
  onSelect,
  disabled = false,
  danger = false,
}: {
  children: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <MenuPrimitive.Item
      className={`menu-item ${danger ? "menu-item-danger" : ""}`}
      onSelect={onSelect}
      disabled={disabled}
    >
      {children}
    </MenuPrimitive.Item>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}
export function Alert({ children }: { children: ReactNode }) {
  return children ? (
    <div className="alert" role="alert">
      {children}
    </div>
  ) : null;
}

export function DirectoryToolbar({
  searchValue,
  onSearchChange,
  searchLabel,
  searchPlaceholder = "筛选…",
  badge,
  create,
  more,
}: {
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchLabel: string;
  searchPlaceholder?: string;
  badge?: ReactNode;
  create?: ReactNode;
  more?: ReactNode;
}) {
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const expanded = focused || searchValue.length > 0;

  return (
    <div className={`directory-toolbar ${expanded ? "is-search-expanded" : ""}`}>
      <div className={`directory-search-box ${expanded ? "is-expanded" : ""}`}>
        <button
          type="button"
          className="directory-search-trigger"
          aria-label={searchLabel}
          title={searchLabel}
          onClick={() => {
            setFocused(true);
            window.setTimeout(() => inputRef.current?.focus(), 0);
          }}
        >
          <Search size={15} />
        </button>
        <input
          ref={inputRef}
          type="search"
          className="directory-search-input"
          aria-label={searchLabel}
          placeholder={searchPlaceholder}
          value={searchValue}
          tabIndex={expanded ? 0 : -1}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onChange={(event) => onSearchChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              if (searchValue) onSearchChange("");
              else inputRef.current?.blur();
            }
          }}
        />
        {searchValue && (
          <button
            type="button"
            className="directory-search-clear"
            aria-label={`清除${searchLabel}`}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              onSearchChange("");
              inputRef.current?.focus();
            }}
          >
            <X size={13} />
          </button>
        )}
      </div>
      {!expanded && (badge || create || more) && (
        <div className="directory-toolbar-actions">
          {badge}
          {create}
          {more}
        </div>
      )}
    </div>
  );
}
