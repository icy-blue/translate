# Examples

## Abstract And Body

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Translate ABSTRACT from the local PDF.",
      "message_kind": "system_prompt",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "# 摘要\n这是摘要译文。",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "ok",
          "units": ["ABSTRACT", "1 INTRODUCTION"],
          "appendix_units": [],
          "reason": ""
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "ok",
          "active_scope": "body",
          "active_units": ["ABSTRACT", "1 INTRODUCTION"],
          "current_unit_id": "ABSTRACT",
          "current_unit_index": 0,
          "completed_unit_ids": ["ABSTRACT"],
          "remaining_unit_ids": ["1 INTRODUCTION"],
          "next_unit_id": "1 INTRODUCTION",
          "state": "IN_PROGRESS",
          "reason": "",
          "total_unit_count": 2,
          "completed_unit_count": 1,
          "source": "canonical_payload",
          "is_completed": false,
          "is_all_done": false
        }
      }
    },
    {
      "role": "user",
      "content": "Continue with 1 INTRODUCTION.",
      "message_kind": "continue_command",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "# 1 引言\n这是引言译文。",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "ok",
          "units": ["ABSTRACT", "1 INTRODUCTION"],
          "appendix_units": [],
          "reason": ""
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "ok",
          "active_scope": "done",
          "active_units": [],
          "current_unit_id": "1 INTRODUCTION",
          "current_unit_index": -1,
          "completed_unit_ids": ["ABSTRACT", "1 INTRODUCTION"],
          "remaining_unit_ids": [],
          "next_unit_id": "",
          "state": "ALL_DONE",
          "reason": "",
          "total_unit_count": 2,
          "completed_unit_count": 2,
          "source": "canonical_payload",
          "is_completed": true,
          "is_all_done": true
        }
      }
    }
  ],
  "first_bot_message": "# 摘要\n这是摘要译文。",
  "continue_count_used": 1,
  "translation_plan": {
    "protocol": "unit_v1",
    "status": "ok",
    "units": ["ABSTRACT", "1 INTRODUCTION"],
    "appendix_units": [],
    "reason": ""
  },
  "translation_status": {
    "protocol": "unit_v1",
    "planner_status": "ok",
    "active_scope": "done",
    "active_units": [],
    "current_unit_id": "1 INTRODUCTION",
    "current_unit_index": -1,
    "completed_unit_ids": ["ABSTRACT", "1 INTRODUCTION"],
    "remaining_unit_ids": [],
    "next_unit_id": "",
    "state": "ALL_DONE",
    "reason": "",
    "total_unit_count": 2,
    "completed_unit_count": 2,
    "source": "canonical_payload",
    "is_completed": true,
    "is_all_done": true
  },
  "errors": []
}
```

## Body And Appendix Completion

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Translate ABSTRACT from the local PDF.",
      "message_kind": "system_prompt",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "# 摘要\n这是摘要译文。",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "ok",
          "units": ["ABSTRACT", "1 INTRODUCTION"],
          "appendix_units": ["APPENDIX A"],
          "reason": ""
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "ok",
          "active_scope": "body",
          "active_units": ["ABSTRACT", "1 INTRODUCTION"],
          "current_unit_id": "ABSTRACT",
          "current_unit_index": 0,
          "completed_unit_ids": ["ABSTRACT"],
          "remaining_unit_ids": ["1 INTRODUCTION"],
          "next_unit_id": "1 INTRODUCTION",
          "state": "IN_PROGRESS",
          "reason": "",
          "total_unit_count": 3,
          "completed_unit_count": 1,
          "source": "canonical_payload",
          "is_completed": false,
          "is_all_done": false
        }
      }
    },
    {
      "role": "user",
      "content": "Continue with 1 INTRODUCTION.",
      "message_kind": "continue_command",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "# 1 引言\n这是引言译文。",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "ok",
          "units": ["ABSTRACT", "1 INTRODUCTION"],
          "appendix_units": ["APPENDIX A"],
          "reason": ""
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "ok",
          "active_scope": "appendix",
          "active_units": ["APPENDIX A"],
          "current_unit_id": "1 INTRODUCTION",
          "current_unit_index": -1,
          "completed_unit_ids": ["ABSTRACT", "1 INTRODUCTION"],
          "remaining_unit_ids": ["APPENDIX A"],
          "next_unit_id": "APPENDIX A",
          "state": "BODY_DONE",
          "reason": "",
          "total_unit_count": 3,
          "completed_unit_count": 2,
          "source": "canonical_payload",
          "is_completed": true,
          "is_all_done": false
        }
      }
    },
    {
      "role": "user",
      "content": "Continue with APPENDIX A.",
      "message_kind": "continue_command",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "# Appendix A 附录\n这是附录译文。",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "ok",
          "units": ["ABSTRACT", "1 INTRODUCTION"],
          "appendix_units": ["APPENDIX A"],
          "reason": ""
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "ok",
          "active_scope": "done",
          "active_units": [],
          "current_unit_id": "APPENDIX A",
          "current_unit_index": -1,
          "completed_unit_ids": ["ABSTRACT", "1 INTRODUCTION", "APPENDIX A"],
          "remaining_unit_ids": [],
          "next_unit_id": "",
          "state": "ALL_DONE",
          "reason": "",
          "total_unit_count": 3,
          "completed_unit_count": 3,
          "source": "canonical_payload",
          "is_completed": true,
          "is_all_done": true
        }
      }
    }
  ],
  "first_bot_message": "# 摘要\n这是摘要译文。",
  "continue_count_used": 2,
  "translation_plan": {
    "protocol": "unit_v1",
    "status": "ok",
    "units": ["ABSTRACT", "1 INTRODUCTION"],
    "appendix_units": ["APPENDIX A"],
    "reason": ""
  },
  "translation_status": {
    "protocol": "unit_v1",
    "planner_status": "ok",
    "active_scope": "done",
    "active_units": [],
    "current_unit_id": "APPENDIX A",
    "current_unit_index": -1,
    "completed_unit_ids": ["ABSTRACT", "1 INTRODUCTION", "APPENDIX A"],
    "remaining_unit_ids": [],
    "next_unit_id": "",
    "state": "ALL_DONE",
    "reason": "",
    "total_unit_count": 3,
    "completed_unit_count": 3,
    "source": "canonical_payload",
    "is_completed": true,
    "is_all_done": true
  },
  "errors": []
}
```

## Unsupported Planner

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Inspect the local PDF and build a translation plan.",
      "message_kind": "system_prompt",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "unsupported",
          "units": [],
          "appendix_units": [],
          "reason": "unreliable_section_boundaries"
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "unsupported",
          "active_scope": "body",
          "active_units": [],
          "current_unit_id": "",
          "current_unit_index": -1,
          "completed_unit_ids": [],
          "remaining_unit_ids": [],
          "next_unit_id": "",
          "state": "UNSUPPORTED",
          "reason": "unreliable_section_boundaries",
          "total_unit_count": 0,
          "completed_unit_count": 0,
          "source": "canonical_payload",
          "is_completed": false,
          "is_all_done": false
        }
      }
    }
  ],
  "first_bot_message": "",
  "continue_count_used": 0,
  "translation_plan": {
    "protocol": "unit_v1",
    "status": "unsupported",
    "units": [],
    "appendix_units": [],
    "reason": "unreliable_section_boundaries"
  },
  "translation_status": {
    "protocol": "unit_v1",
    "planner_status": "unsupported",
    "active_scope": "body",
    "active_units": [],
    "current_unit_id": "",
    "current_unit_index": -1,
    "completed_unit_ids": [],
    "remaining_unit_ids": [],
    "next_unit_id": "",
    "state": "UNSUPPORTED",
    "reason": "unreliable_section_boundaries",
    "total_unit_count": 0,
    "completed_unit_count": 0,
    "source": "canonical_payload",
    "is_completed": false,
    "is_all_done": false
  },
  "errors": []
}
```

## Partial Ambiguous Later Unit

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Translate ABSTRACT from the local PDF.",
      "message_kind": "system_prompt",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "# 摘要\n这是摘要译文。",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "ok",
          "units": ["ABSTRACT", "2 METHOD", "3 RESULTS"],
          "appendix_units": [],
          "reason": ""
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "ok",
          "active_scope": "body",
          "active_units": ["ABSTRACT", "2 METHOD", "3 RESULTS"],
          "current_unit_id": "ABSTRACT",
          "current_unit_index": 0,
          "completed_unit_ids": ["ABSTRACT"],
          "remaining_unit_ids": ["2 METHOD", "3 RESULTS"],
          "next_unit_id": "2 METHOD",
          "state": "IN_PROGRESS",
          "reason": "",
          "total_unit_count": 3,
          "completed_unit_count": 1,
          "source": "canonical_payload",
          "is_completed": false,
          "is_all_done": false
        }
      }
    },
    {
      "role": "user",
      "content": "Continue with 2 METHOD.",
      "message_kind": "continue_command",
      "visible_to_user": false
    },
    {
      "role": "bot",
      "content": "",
      "message_kind": "bot_reply",
      "visible_to_user": true,
      "section_category": null,
      "client_payload": {
        "translation_plan": {
          "protocol": "unit_v1",
          "status": "ok",
          "units": ["ABSTRACT", "2 METHOD", "3 RESULTS"],
          "appendix_units": [],
          "reason": ""
        },
        "translation_status": {
          "protocol": "unit_v1",
          "planner_status": "ok",
          "active_scope": "body",
          "active_units": ["ABSTRACT", "2 METHOD", "3 RESULTS"],
          "current_unit_id": "2 METHOD",
          "current_unit_index": 1,
          "completed_unit_ids": ["ABSTRACT"],
          "remaining_unit_ids": ["2 METHOD", "3 RESULTS"],
          "next_unit_id": "",
          "state": "UNSUPPORTED",
          "reason": "ambiguous_unit_boundary",
          "total_unit_count": 3,
          "completed_unit_count": 1,
          "source": "canonical_payload",
          "is_completed": false,
          "is_all_done": false
        }
      }
    }
  ],
  "first_bot_message": "# 摘要\n这是摘要译文。",
  "continue_count_used": 1,
  "translation_plan": {
    "protocol": "unit_v1",
    "status": "ok",
    "units": ["ABSTRACT", "2 METHOD", "3 RESULTS"],
    "appendix_units": [],
    "reason": ""
  },
  "translation_status": {
    "protocol": "unit_v1",
    "planner_status": "ok",
    "active_scope": "body",
    "active_units": ["ABSTRACT", "2 METHOD", "3 RESULTS"],
    "current_unit_id": "2 METHOD",
    "current_unit_index": 1,
    "completed_unit_ids": ["ABSTRACT"],
    "remaining_unit_ids": ["2 METHOD", "3 RESULTS"],
    "next_unit_id": "",
    "state": "UNSUPPORTED",
    "reason": "ambiguous_unit_boundary",
    "total_unit_count": 3,
    "completed_unit_count": 1,
    "source": "canonical_payload",
    "is_completed": false,
    "is_all_done": false
  },
  "errors": [
    {
      "skill": "self-translate-full-paper-skill",
      "type": "warning",
      "message": "Stopped after ambiguous unit boundary at 2 METHOD.",
      "retryable": false
    }
  ]
}
```
