# Copyright (C) 2015  Vincent Pelletier <plr.vincent@gmail.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
from ply.yacc import *

def startPush(self):
    """
    Incremental parsing: Reinitialise parser state.
    """
    self._push_pslice = pslice = YaccProduction(None)
    self._push_error_count = 0
    pslice.parser = self
    self.statestack = [0]
    end = YaccSymbol()
    end.type = '$end'
    self.symstack = [end]
LRParser.startPush = startPush

def push(self, token):
    """
    Incremental parsing: Update parser state with given token.
    """
    if token is None:
        token = YaccSymbol()
        token.type = '$end'
    symstack = self.symstack
    statestack = self.statestack
    actions = self.action
    token_type = token.type
    productions = self.productions
    pslice = self._push_pslice
    goto = self.goto
    while True:
        state_actions = actions[statestack[-1]]
        try:
            t = state_actions[token_type]
        except KeyError:
            errorcount = self._push_error_count
            if errorcount == 0 or self.errorok:
                self.errorok = 0
                errtoken = token
                if token_type == '$end':
                    errtoken = None
                if self.errorfunc:
                    tok = self.errorfunc(errtoken)
                    if self.errorok:
                        # For compatibility with ply.yacc API.
                        # errorfunc could call push instead.
                        self.push(tok)
                        return
                else:
                    if errtoken:
                        sys.stderr.write("yacc: Syntax error at line %d, token=%s\n" % (getattr(token, 'lineno', 0), token_type))
                    else:
                        sys.stderr.write("yacc: Parse error in input. EOF\n")
                        return
            else:
                self._push_error_count = error_count
            if token_type == "$end":
                return
            elif len(statestack) <= 1:
                statestack[:] = [0]
                return
            elif token_type == 'error':
                symstack.pop()
                statestack.pop()
            else:
                sym = symstack[-1]
                if sym.type == 'error':
                    return
                t = YaccSymbol()
                t.type = 'error'
                if hasattr(token, "lineno"):
                    t.lineno = token.lineno
                t.value = token
                self.push(t)
            continue
        if t > 0:
            # shift a symbol on the stack
            statestack.append(t)
            symstack.append(token)
            if self._push_error_count:
                self._push_error_count -= 1
        elif t < 0:
            # reduce a symbol on the stack
            p = productions[-t]
            pname = p.name
            plen = p.len
            sym = YaccSymbol()
            sym.name = pname
            sym.value = None
            sym.type = None
            if plen:
                targ = symstack[-plen-1:]
                targ[0] = sym
                del symstack[-plen:]
                del statestack[-plen:]
            else:
                targ = [sym]
            pslice.slice = targ
            p.callable(pslice)
            statestack.append(goto[statestack[-1]][pname])
            symstack.append(sym)
            continue
        break
LRParser.push = push
