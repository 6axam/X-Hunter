import type { ButtonHTMLAttributes, HTMLAttributes, PropsWithChildren } from "react";
import { clsx } from "clsx";

export function Button({ className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={clsx("button", className)} {...props} />;
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={clsx("card", className)} {...props} />;
}

export function Pill({ children, tone = "neutral" }: PropsWithChildren<{ tone?: string }>) {
  return <span className={`pill pill--${tone}`}>{children}</span>;
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={clsx("skeleton", className)} />;
}

