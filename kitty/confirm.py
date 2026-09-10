# ttydnd confirmation dialog.
# kitty's ask kitten frames the default choice alone and binds no arrow keys,
# so the buttons are drawn here.
# Focus follows arrows, Tab and the pointer, and the framed button is the one Enter takes.
# Spec arrives as JSON in the last argument, the chosen letter comes back as the response.

import json

from kittens.tui.handler import Handler
from kittens.tui.loop import Loop
from kittens.tui.operations import MouseTracking, styled

GOLD = 'yellow'
LEFT_PAD = 2
GAP = 2


class Confirm(Handler):
    # full: motion events arrive, so the frame can follow the pointer.
    mouse_tracking = MouseTracking.full

    def __init__(self, spec):
        self.message = spec['message'].split('\n')
        self.choices = spec['choices']
        self.index = next(
            (i for i, c in enumerate(self.choices) if c['letter'] == spec['default']), 0
        )
        self.response = ''
        # (x1, x2, y, index) in cells, rebuilt on every draw.
        self.boxes = []

    def initialize(self):
        self.cmd.set_cursor_visible(False)
        self.draw_screen()

    def finalize(self):
        self.cmd.set_cursor_visible(True)

    def label(self, choice, focused):
        text = f' {choice["label"]} '
        letter = choice['letter']
        cut = choice['label'].lower().index(letter.lower())
        head, key, tail = text[:cut + 1], text[cut + 1], text[cut + 2:]
        key = styled(key, fg=choice['color'], bold=True)
        return head + key + tail, len(text)

    @Handler.atomic_update
    def draw_screen(self):
        self.cmd.clear_screen()
        for line in self.message:
            self.print(' ' * LEFT_PAD + line)
        self.print()

        rows = ['', '', '']
        self.boxes = []
        x = LEFT_PAD
        y = len(self.message) + 1
        for i, choice in enumerate(self.choices):
            focused = i == self.index
            text, width = self.label(choice, focused)
            frame = (lambda s: styled(s, fg=GOLD)) if focused else (lambda s: s)
            rows[0] += frame('╭' + '─' * width + '╮') + ' ' * GAP
            rows[1] += frame('│') + text + frame('│') + ' ' * GAP
            rows[2] += frame('╰' + '─' * width + '╯') + ' ' * GAP
            self.boxes.append((x, x + width + 1, y + 1, i))
            x += width + 2 + GAP
        for row in rows:
            self.print(' ' * LEFT_PAD + row)

    def accept(self, letter):
        self.response = letter
        self.quit_loop(0)

    # Tab and Enter carry text, and Handler.on_key_event would route those to on_text alone.
    def on_key_event(self, key_event, in_bracketed_paste=False):
        if key_event.matches('esc') or key_event.matches('ctrl+c'):
            self.quit_loop(1)
        elif key_event.matches('enter') or key_event.matches('kp_enter'):
            self.accept(self.choices[self.index]['letter'])
        elif key_event.matches('left') or key_event.matches('shift+tab'):
            self.index = (self.index - 1) % len(self.choices)
            self.draw_screen()
        elif key_event.matches('right') or key_event.matches('tab'):
            self.index = (self.index + 1) % len(self.choices)
            self.draw_screen()
        elif key_event.text:
            self.on_text(key_event.text, in_bracketed_paste)

    def on_text(self, text, in_bracketed_paste=False):
        # A terminal without the kitty keyboard protocol sends Tab and Enter as text.
        if text in ('\r', '\n'):
            self.accept(self.choices[self.index]['letter'])
            return
        if text == '\t':
            self.index = (self.index + 1) % len(self.choices)
            self.draw_screen()
            return
        for choice in self.choices:
            if text.lower() == choice['letter'].lower():
                self.accept(choice['letter'])
                return

    def hit(self, mouse_event):
        for x1, x2, y, i in self.boxes:
            if y == mouse_event.cell_y and x1 <= mouse_event.cell_x <= x2:
                return i
        return None

    def on_mouse_move(self, mouse_event):
        i = self.hit(mouse_event)
        if i is not None and i != self.index:
            self.index = i
            self.draw_screen()

    def on_click(self, mouse_event):
        i = self.hit(mouse_event)
        if i is not None:
            self.accept(self.choices[i]['letter'])

    def on_interrupt(self):
        self.quit_loop(1)

    def on_eot(self):
        self.quit_loop(1)


def main(args):
    handler = Confirm(json.loads(args[-1]))
    Loop().loop(handler)
    return {'response': handler.response}


def handle_result(args, data, target_window_id, boss):
    return data
