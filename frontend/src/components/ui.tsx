import {
  useId,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type TextareaHTMLAttributes,
  type ReactNode,
} from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as SelectPrimitive from "@radix-ui/react-select";
import * as MenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, ChevronRight, MoreHorizontal, X } from "lucide-react";

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
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label?: string }) {
  const id = useId();
  return (
    <label className={`field ${className}`} htmlFor={id}>
      {label && <span>{label}</span>}
      <input id={id} {...props} />
    </label>
  );
}
export function Textarea({
  label,
  className = "",
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }) {
  const id = useId();
  return (
    <label className={`field ${className}`} htmlFor={id}>
      {label && <span>{label}</span>}
      <textarea id={id} {...props} />
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
  className = "",
}: {
  title: string;
  description?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  wide?: boolean;
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
          <div className="dialog-heading">
            <DialogPrimitive.Title>{title}</DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" aria-label="关闭">
                <X size={18} />
              </Button>
            </DialogPrimitive.Close>
          </div>
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
export function Menu({
  label,
  children,
  iconOnly = false,
  align = "end",
  triggerClassName = "",
}: {
  label: string;
  children: ReactNode;
  iconOnly?: boolean;
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
            <MoreHorizontal size={17} />
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
