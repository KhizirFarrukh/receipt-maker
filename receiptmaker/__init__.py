"""Receipt Generator.

The code is grouped by what it is responsible for, and the groups only ever
depend downwards:

    core     paths, config, money, the template engine
    pricing  what a line comes to: amounts, per-unit values, instalments,
             shipments, payment charges
    storage  everything that outlives a run: products, history, the invoice
             counter, drafts, CSV import/export
    output   turning a receipt into a document: render, sign, the service that
             sequences the two
    ui       tkinter, and nothing else imports it
    tools    the headless entry points (cli, keygen, verify_receipt)

`ui` may import anything; nothing may import `ui`. That is what keeps the
render path free of tkinter, which the golden gate and Stage1Layering assert.
"""
