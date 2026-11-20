"""A small popup that offers to run one action as administrator (Windows then shows its own approval prompt)."""
import customtkinter as ctk


def ask_admin(parent, title, message, on_yes):
    win = ctk.CTkToplevel(parent)
    win.title("Administrator needed")
    root = parent.winfo_toplevel()
    x = root.winfo_rootx() + max((root.winfo_width() - 440) // 2, 0)
    y = root.winfo_rooty() + max((root.winfo_height() - 230) // 2, 0)
    win.geometry(f"440x230+{x}+{y}")
    win.resizable(False, False)
    win.after(150, win.focus)
    ctk.CTkLabel(win, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=22, pady=(20, 4))
    ctk.CTkLabel(win, text=message, anchor="w", justify="left", wraplength=396, text_color="gray80").pack(
        fill="x", padx=22)
    buttons = ctk.CTkFrame(win, fg_color="transparent")
    buttons.pack(side="bottom", fill="x", padx=22, pady=18)

    def yes():
        win.destroy()
        on_yes()

    ctk.CTkButton(buttons, text="Run as administrator", width=190, command=yes).pack(side="left")
    ctk.CTkButton(buttons, text="Cancel", width=90, fg_color="gray30", hover_color="gray40",
                  command=win.destroy).pack(side="left", padx=10)
    return win
